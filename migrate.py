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

import argparse
import urllib2
import getpass
import logging
import sys

from github import Github
from github import GithubException

logging.basicConfig(level = logging.ERROR)

try:
    import json
except ImportError:
    import simplejson as json

def output(string):
    sys.stdout.write(string)
    sys.stdout.flush()

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
        help="GitHub to add issues to. Format: <username>/<repo name>"
    )

    parser.add_argument(
        "-n", "--dry-run",
        action="store_true", dest="dry_run", default=False,
        help="Perform a dry run and print eveything."
    )

    parser.add_argument(
        "-f", "--start", type=int, dest="start", default=0,
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
    return u"""{}

{}
- Bitbucket: https://bitbucket.org/{}/{}/issue/{}
- Originally reported by: {}
- Originally created at: {}
""".format(
        content,
        '-' * 40,
        options.bitbucket_username, options.bitbucket_repo, issue['local_id'],
        format_name(issue),
        issue['created_on']
    )


def format_comment(comment):
    return u"""{}

{}
Original comment by: {}
""".format(
        comment['body'],
        '-' * 40,
        comment['user'].encode('utf-8')
    )


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
    return "\n".join(lines)


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
    result = json.loads(urllib2.urlopen(url).read())
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
                'body': body.encode('utf-8'),
                'number': comment['comment_id']
            })

    return comments


def github_label(name, color = "FFFFFF"):
    """ Returns the Github label with the given name, creating it if necessary. """

    try:
        return label_cache[name]
    except KeyError:
        try:
            return label_cache.setdefault(name, github_repo.get_label(name))
        except GithubException:
            return label_cache.setdefault(name, github_repo.create_label(name, color))


def add_comments_to_issue(github_issue, bitbucket_comments):
    """ Migrates all comments from a Bitbucket issue to its Github copy. """

    # Retrieve existing Github comments, to figure out which Google Code comments are new
    existing_comments = [comment.body for comment in github_issue.get_comments()]

    if len(bitbucket_comments) > 0:
        output(", adding comments")

    for i, comment in enumerate(bitbucket_comments):
        body = u'_From {user} on {created_at}_\n\n{body}'.format(**comment)
        if body in existing_comments:
            logging.info('Skipping comment %d: already present', i + 1)
        else:
            logging.info('Adding comment %d', i + 1)
            if not options.dry_run:
                github_issue.create_comment(body.encode('utf-8'))
                output('.')
    output('\n')


# GitHub push
def push_issue(gh_username, gh_repository, issue, body):
    """ Migrates the given Bitbucket issue to Github. """

    body = issue['content'].replace('%', '&#37;')

    output('Adding issue [%d]: %s' % (issue.get('local_id'), issue.get('title').encode('utf-8')))

    github_labels = []
    # Set the status and labels
    if issue.get('status') == 'resolved':
        pass
    # Everything else is done with labels in github
    else:
        github_labels = [github_label(issue['status'])]

    github_issue = None
    if not options.dry_run:
        github_issue = github_repo.create_issue(issue['title'], body = body.encode('utf-8'), labels = github_labels)
    
    # Set the status and labels
    if issue.get('status') == 'resolved':
        github_issue.edit(state = 'closed')

    # Milestones

    return github_issue


if __name__ == "__main__":
    options = read_arguments()
    bb_url = "https://bitbucket.org/api/1.0/repositories/{}/{}/issues".format(
        options.bitbucket_username,
        options.bitbucket_repo
    )

    # Cache Github tags, to avoid unnecessary API requests
    label_cache = {}

    google_project_name = options.github_repo

    # fetch issues from Bitbucket
    issues = get_issues(bb_url, options.start)

    while True:
        github_password = getpass.getpass("Github password: ")
        try:
            Github(options.github_username, github_password).get_user().login
            break
        except Exception:
            output("Bad credentials, try again.\n")

    github = Github(options.github_username, github_password)

    github_user = github.get_user()

    # If the project name is specified as owner/project, assume that it's owned by either
    # a different user than the one we have credentials for, or an organization.

    if "/" in google_project_name:
        gh_username, gh_repository = google_project_name.split('/')
        try:
            github_owner = github.get_user(gh_username)
        except GithubException:
            try:
                github_owner = github.get_organization(gh_username)
            except GithubException:
                github_owner = github_user
    else:
        github_owner = github_user

    github_repo = github_owner.get_repo(gh_repository)

    # Sort issues, to sync issue numbers on freshly created GitHub projects.
    # Note: not memory efficient, could use too much memory on large projects.
    for issue in sorted(issues, key=lambda issue: issue['local_id']):
        body = format_body(options, issue).encode('utf-8')
        github_issue = push_issue(gh_username, gh_repository, issue, body)
        
        if github_issue:
            comments = get_comments(bb_url, issue)
            add_comments_to_issue(github_issue, comments)

    output("Created {} issues\n".format(len(issues)))