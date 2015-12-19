#!/usr/bin/env python
#-*- coding: utf-8 -*-

# This file is part of the Bitbucket issue migration script.
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
# along with the Bitbucket issue migration script.
# If not, see <http://www.gnu.org/licenses/>.


import argparse
import urllib2
import getpass
import json

import re
import requests


def read_arguments():
    parser = argparse.ArgumentParser(
        description=(
            "A tool to migrate issues from Bitbucket to GitHub.\n"
            "Note: The Bitbucket repository and issue tracker have to be "
            "public"
        )
    )

    parser.add_argument(
        "bitbucket_username",
        help="Your Bitbucket username"
    )

    parser.add_argument(
        "bitbucket_repo",
        help="Bitbucket repository to pull data from."
    )

    parser.add_argument(
        "github_username",
        help="Your GitHub username"
    )

    parser.add_argument(
        "github_repo",
        help=(
            "GitHub repository to add issues to.\n"
            "Format: <username>/<repo name>"
        )
    )

    parser.add_argument(
        "-n", "--dry-run",
        action="store_true", dest="dry_run", default=False,
        help="Perform a dry run and print eveything."
    )

    parser.add_argument(
        "-f", "--start_id", type=int, dest="start", default=0,
        help="Bitbucket issue ID from which to start the import"
    )

    return parser.parse_args()


# Formatters
def format_user(author_info):
    result = ''
    if author_info and author_info['first_name'] and author_info['last_name']:
        result = u" ".join([author_info['first_name'], author_info['last_name']])

    if author_info and 'username' in author_info:
        link1 = '[{0}](http://bitbucket.org/{0})'.format(author_info['username'])
        link2 = '[{0}](http://github.com/{0})'.format(author_info['username'])
        links = 'Bitbucket: {}, GitHub: {}'.format(link1, link2)
        if result:
            result += ' ({})'.format(links)
        else:
            result = links

    if not result:
        result = "Anonymous"

    return result


def format_name(issue):
    if 'reported_by' in issue:
        return format_user(issue['reported_by'])
    else:
        return "Anonymous"


def format_body(options, issue):
    content = clean_body(issue.get('content'))
    content = fix_links(options, content)
    return u"""Originally reported by: **{reporter}**

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
    )


def format_comment(options, comment):
    return u"""*Original comment by* **{}**:

{}
{}
""".format(
        comment['user'],
        '-' * 40,
        fix_links(options, clean_comment(comment['body'])),
    )


def fix_links(options, content):
    """
    Fix explicit links found in the body of a comment or issue to use
    relative links ("#<id>").
    """
    pattern = r'https://bitbucket.org/{user}/{repo}/issue/(\d+)'.format(
        user=options.bitbucket_username, repo=options.bitbucket_repo)
    return re.sub(pattern, r'#\1', content)


def format_date(bb_date):
    """
    Convert from one of the various date formats used by Bitbucket to
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
    for line in unicode(body).splitlines():
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
    return u"\n".join(lines)


def clean_comment(body):
    lines = body.splitlines()
    clean_changesets(lines)
    return u"\n".join(lines)


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


# Bitbucket fetch
def get_issues(bb_url, start_id):
    '''
    Fetch the issues from Bitbucket
    '''
    issues = []

    while True:
        url = "{}/?start={}".format(
            bb_url,
            start_id
        )

        try:
            response = urllib2.urlopen(url)
        except urllib2.HTTPError as ex:
            ex.message = (
                'Problem trying to connect to bitbucket ({url}): {ex} '
                'Hint: the bitbucket repository name is case-sensitive.'
                .format(url=url, ex=ex)
            )
            raise
        else:
            result = json.loads(response.read())
            if not result['issues']:
                # Check to see if there is issues to process if not break out.
                break

            issues += result['issues']
            start_id += len(result['issues'])

    return issues


def get_comments(bb_url, issue):
    '''
    Fetch the comments for a Bitbucket issue
    '''
    url = "{}/{}/comments/".format(
        bb_url,
        issue['local_id']
    )
    result = json.loads(urllib2.urlopen(url).read(), encoding='utf-8')
    ordered = sorted(result, key=lambda comment: comment["utc_created_on"])

    comments = []
    for comment in ordered:
        body = comment['content'] or ''

        # Status comments (assigned, version, etc. changes) have in bitbucket
        # no body
        if body:
            comments.append({
                'user': format_user(comment['author_info']),
                'created_at': comment['utc_created_on'],
                'body': body,
                'number': comment['comment_id']
            })

    return comments


# GitHub push
def push_issue(auth, gh_username, gh_repository, issue, body, comments, options):
    # Using the normal Issue API to import all issues will easily generate
    # an HTTP error, as explained by GitHub support:
    #   Creating issues and
    #   comments via the API triggers email notifications just like they'd be
    #   triggered when you create content via the Web UI. The abuse rate limit
    #   was introduced in order to prevent spikes in notification emails which
    #   cause problems for users and our infrastructure.
    # This code uses the Issue Import API instead:
    # https://github.com/nicoddemus/bitbucket_issue_migration/issues/1

    comments_data = [
        {
            'body': format_comment(options, x),
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
        user=gh_username, repo=gh_repository)
    headers = {'Accept': 'application/vnd.github.golden-comet-preview+json'}
    respo = requests.post(url, json=issue_data, auth=auth, headers=headers)
    if respo.status_code in (200, 202):
        print u"Created bitbucket issue {}: {} [{} comments]".format(
            issue['local_id'],
            issue['title'].encode('ascii', errors='replace'),
            len(comments),
        )
    else:
        raise RuntimeError(u"Failed to create issue: {}".format(issue['local_id']))


if __name__ == "__main__":
    options = read_arguments()
    bb_url = "https://api.bitbucket.org/1.0/repositories/{}/{}/issues".format(
        options.bitbucket_username,
        options.bitbucket_repo
    )

    # ask for password so the user doesn't have to sit around waiting
    # to provide some initial input
    github_password = getpass.getpass("Please enter your GitHub password\n")

    # fetch issues from Bitbucket
    issues = get_issues(bb_url, options.start)

    # push them in GitHub (issues comments are fetched here)
    gh_username, gh_repository = options.github_repo.split('/')
    auth = (gh_username, github_password)

    # Sort issues, to sync issue numbers on freshly created GitHub projects.
    # Note: not memory efficient, could use too much memory on large projects.
    issues = sorted(issues, key=lambda issue: issue['local_id'])
    for index, issue in enumerate(issues):
        comments = get_comments(bb_url, issue)

        if options.dry_run:
            print u"Title: {}".format(issue.get('title'))
            print u"Body: {}".format(
                format_body(options, issue)
            )
            print u"Comments", [comment['body'].encode('utf-8', errors='replace') for comment in comments]
        else:
            body = format_body(options, issue)
            push_issue(auth, gh_username, gh_repository, issue, body,
                       comments, options)
            print "Created {} of {} issues".format(index + 1, len(issues))
