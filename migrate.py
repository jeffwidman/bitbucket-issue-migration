#!/usr/bin/env python
#-*- coding: utf-8 -*-

# This file is part of the bitbucket issue migration script.
#
# The script is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# The script is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with the bitbucket issue migration script.
# If not, see <http://www.gnu.org/licenses/>.

from __future__ import print_function, unicode_literals

import argparse
import getpass
import operator
import itertools
import re
import json

import github
import requests

try:
    import keyring
except ImportError:
    # keyring isn't available, so mock the interface to simulate no pw
    class Keyring:
        get_password = staticmethod(lambda system, username: None)

from six import text_type
from six.moves import urllib
from jaraco.itertools import Counter


def read_arguments():
    parser = argparse.ArgumentParser(
        description=(
            "A tool to migrate issues from Bitbucket to GitHub.\n"
            "note: the Bitbucket repository and issue tracker have to be"
            "public"
        )
    )

    parser.add_argument(
        "bitbucket_username",
        help="Bitbucket username of the source repository."
    )

    parser.add_argument(
        "bitbucket_repo",
        help="Bitbucket project name for the source repo."
    )

    parser.add_argument(
        "github_username",
        help="Your GitHub username"
    )

    parser.add_argument(
        "github_repo",
        help="GitHub to add issues to. Format: <username>/<repo name>"
    )

    parser.add_argument(
        "-n", "--dry-run",
        action="store_true", dest="dry_run", default=False,
        help="Perform a dry run and print eveything."
    )

    parser.add_argument(
        "-f", "--start_id", type=int, dest="start", default=0,
        help="Bitbucket issue id from which to start import"
    )

    return parser.parse_args()


# Formatters
def format_user(author_info):
    if not author_info:
        return "Anonymous"

    if author_info['first_name'] and author_info['last_name']:
        return " ".join([author_info['first_name'], author_info['last_name']])

    if 'username' in author_info:
        return '[{0}](http://bitbucket.org/{0})'.format(
            author_info['username']
        )


def format_name(issue):
    if 'reported_by' in issue:
        return format_user(issue['reported_by'])
    else:
        return "Anonymous"


def format_body(options, issue):
    content = clean_body(issue.get('content'))
    return """Originally reported by: **{reporter}**
Originally created at: **{created}**

{sep}

{content}

{sep}
- Bitbucket: https://bitbucket.org/{user}/{repo}/issue/{id}
""".format(
        reporter=format_name(issue),
        sep='-' * 40,
        content=content,
        user=options.bitbucket_username,
        repo=options.bitbucket_repo,
        id=issue['local_id'],
        created=issue['created_on'],
    )


def format_comment(comment):
    return """{}

{}
Original comment by: {}
""".format(
        clean_comment(comment['body']),
        '-' * 40,
        comment['user'],
    )


def format_date(bb_date):
    """
    Convert from one of the various date formats used by BitBucket to
    the one supported by GitHub.
    """
    # u'2010-10-12T13:14:44.584'
    m = re.search(r'(\d\d\d\d-\d\d-\d\d)T(\d\d:\d\d:\d\d)', bb_date)
    if m:
        return '{}T{}Z'.format(m.group(1), m.group(2))
    # u'2012-11-26 09:59:39+00:00'
    m = re.search(r'(\d\d\d\d-\d\d-\d\d) (\d\d:\d\d:\d\d)', bb_date)
    if m:
        return '{}T{}Z'.format(m.group(1), m.group(2))

    raise RuntimeError('Could not parse date: {}'.format(bb_date))


def clean_body(body):
    lines = []
    in_block = False
    for line in text_type(body).splitlines():
        if line.startswith("{{{") or line.startswith("}}}"):
            if "{{{" in line:
                before, part, after = line.partition("{{{")
                lines.append('    ' + after)
                in_block = True

            if "}}}" in line:
                before, part, after = line.partition("}}}")
                lines.append('    ' + before)
                in_block = False
        else:
            if in_block:
                lines.append("    " + line)
            else:
                lines.append(line.replace("{{{", "`").replace("}}}", "`"))

    clean_changesets(lines)
    return "\n".join(lines)


def clean_comment(body):
    lines = body.splitlines()
    clean_changesets(lines)
    return "\n".join(lines)


def clean_changesets(lines):
    """
    Clean changeset references like:

        → <<cset 22f3981d50c8>>'

    Since they point to mercurial changesets and there's no easy way to map them
    to git hashes, better to remove them altogether.
    """
    for index, line in reversed(list(enumerate(lines))):
        if line.startswith(u'→ <<cset'):
            lines.pop(index)


def get_comments(bb_url, issue):
    '''
    Fetch the comments for a Bitbucket issue
    '''
    url = "{bb_url}/{issue[local_id]}/comments/".format(**locals())
    result = json.loads(urllib.request.urlopen(url).read().decode('utf-8'))
    by_creation_date = operator.itemgetter("utc_created_on")
    ordered = sorted(result, key=by_creation_date)
    # filter only those that have content; status comments (assigned,
    # version, etc.) have no body
    filtered = filter(operator.itemgetter('content'), ordered)
    return list(map(_parse_comment, filtered))


def _parse_comment(comment):
    """
    Parse a comment as returned from Bitbucket API.
    """
    return dict(
        user=format_user(comment['author_info']),
        created_at=comment['utc_created_on'],
        body=comment['content'],
        number=comment['comment_id'],
    )


class Handler(object):
    bb_base = "https://api.bitbucket.org/1.0/repositories/"
    bb_tmpl = bb_base + "{bitbucket_username}/{bitbucket_repo}/issues"

    def __init__(self, options):
        self.options = options
        self.bb_url = self.bb_tmpl.format(**vars(options))

    @classmethod
    def best(cls):
        options = read_arguments()
        handler_cls = ImportHandler if not options.dry_run else DryRunHandler
        return handler_cls(options)

    def get_issues(self):
        issues = self._iter_issues(self.options.start)
        # In order to sync issue numbers on a freshly-created Github project,
        # sort the issues by local_id
        # Note: not memory efficient and could use too much memory on large
        # projects.
        by_local_id = operator.itemgetter('local_id')
        return sorted(issues, key=by_local_id)

    def run(self):
        self.issues = Counter(self.get_issues())
        for issue in self.issues:
            self.handle(issue)
        print("Created", self.issues.count, "issues")

    def get_comments(self, issue):
        return get_comments(self.bb_url, issue)

    def _iter_issues(self, start_id):
        '''
        Fetch the issues from Bitbucket, one page at a time.
        '''
        url = "{self.bb_url}/?start={start_id}".format(**locals())

        try:
            response = urllib.request.urlopen(url)
        except urllib.error.HTTPError as ex:
            ex.message = (
                'Problem trying to connect to bitbucket ({url}): {ex} '
                'Hint: the bitbucket repository name is case-sensitive.'
                .format(url=url, ex=ex)
            )
            raise

        result = json.loads(response.read().decode('utf-8'))
        if not result['issues']:
            # No issues encountered at or above start_id
            raise StopIteration()

        next_start = start_id + len(result['issues'])
        res = itertools.chain(result['issues'], self._iter_issues(next_start))
        for item in res:
            yield item


class SubmitHandler(Handler):
    def run(self):
        # push them in GitHub (issues comments are fetched here)
        self.github_password = (
            keyring.get_password('Github', self.options.github_username) or
            getpass.getpass("Please enter your GitHub password\n")
        )
        self.github = github.Github(
            login_or_token=self.options.github_username,
            password=self.github_password,
        )
        return super(SubmitHandler, self).run()

    def handle(self, issue):
        comments = self.get_comments(issue)
        body = format_body(self.options, issue)
        self.push_issue(issue, body, comments)

    def push_issue(self, issue, body, comments):
        # Create the issue
        repo_path = self.options.github_repo
        issue_data = {
            'title': issue['title'],
            'body': body
        }
        repo = self.github.get_repo(repo_path)
        new_issue = repo.create_issue(**issue_data)

        # Set the status and labels
        if issue['status'] == 'resolved':
            new_issue.edit(state='closed')

        # Everything else is done with labels in github
        elif issue['status'] == 'wontfix':
            new_issue.edit(state='closed')
        elif issue['status'] == 'on hold':
            pass
        elif issue['status'] == 'invalid':
            new_issue.edit(state='closed')
        elif issue['status'] == 'duplicate':
            new_issue.edit(state='closed')

        # Milestones

        # Add the comments
        for comment in comments:
            new_issue.create_comment(format_comment(comment))

        print("Created: {} [{} comments]".format(
            issue['title'], len(comments)
        ))


class ImportHandler(SubmitHandler):

    @property
    def auth(self):
        return self.options.github_username, self.github_password

    def push_issue(self, issue, body, comments):
        """
        Use the Issue Import API to avoid HTTP errors due to GitHub throttling.

        https://github.com/nicoddemus/bitbucket_issue_migration/issues/1
        """

        comments_data = [
            {
                'body': format_comment(self.options, x),
                'created_at': format_date(x['created_at']),
            } for x in comments]

        issue_data = {
            'issue': {
                'title': issue.get('title'),
                'body': body,
                'closed': issue.get('status') not in ('open', 'new'),
                'created_at': format_date(issue['created_on']),
            },
            'comments': comments_data,
        }

        labels = []
        if issue['metadata']['kind']:
            labels.append(issue['metadata']['kind'])
        if issue['metadata']['component']:
            labels.append(issue['metadata']['component'])
        if labels:
            issue_data['issue']['labels'] = labels

        url = 'https://api.github.com/repos/{user}/{repo}/import/issues'.format(
            user=self.options.github_username, repo=self.options.github_repo)
        headers = {'Accept': 'application/vnd.github.golden-comet-preview+json'}
        respo = requests.post(url, json=issue_data, auth=self.auth, headers=headers)
        if respo.status_code in (200, 202):
            print("Created bitbucket issue {}: {} [{} comments]".format(
                issue['local_id'],
                issue['title'].encode('ascii', errors='replace'),
                len(comments),
            ))
        else:
            raise RuntimeError("Failed to create issue: {}".format(issue['local_id']))


class DryRunHandler(Handler):
    def handle(self, issue):
        comments = self.get_comments(issue)
        body = format_body(self.options, issue)
        print("Title: {}".format(issue['title']))
        print("Body: {}".format(body))
        list(map(format_comment, comments))
        print("Comments", [comment['body'] for comment in comments])


if __name__ == "__main__":
    Handler.best().run()
