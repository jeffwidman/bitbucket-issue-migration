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

from pygithub3 import Github

try:
    import json
except ImportError:
    import simplejson as json


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

    comments = []
    for comment in result:
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


# GitHub push
def push_issue(gh_username, gh_repository, issue, body, comments):
    # Create the issue
    issue_data = {
        'title': issue.get('title').encode('utf-8'),
        'body': body
    }
    new_issue = github.issues.create(
        issue_data,
        gh_username,
        gh_repository
    )

    # Set the status and labels
    if issue.get('status') == 'resolved':
        github.issues.update(
            new_issue.number,
            {'state': 'closed'},
            user=gh_username,
            repo=gh_repository
        )

    # Everything else is done with labels in github
    # TODO: there seems to be a problem with the add_to_issue method of
    #       pygithub3, so it's not possible to assign labels to issues
    elif issue.get('status') == 'wontfix':
        pass
    elif issue.get('status') == 'on hold':
        pass
    elif issue.get('status') == 'invalid':
        pass
    elif issue.get('status') == 'duplicate':
        pass
    elif issue.get('status') == 'wontfix':
        pass

    # github.issues.labels.add_to_issue(
    #     new_issue.number,
    #     issue['metadata']['kind'],
    #     user=gh_username,
    #     repo=gh_repository
    # )

    # github.issues.labels.add_to_issue(
    #     new_issue.number,
    #     gh_username,
    #     gh_repository,
    #     ('import',)
    # )

    # Milestones

    # Add the comments
    for comment in comments:
        github.issues.comments.create(
            new_issue.number,
            format_comment(comment),
            gh_username,
            gh_repository
        )

    print u"Created: {} [{} comments]".format(
        issue['title'], len(comments)
    )


if __name__ == "__main__":
    options = read_arguments()
    bb_url = "https://api.bitbucket.org/1.0/repositories/{}/{}/issues".format(
        options.bitbucket_username,
        options.bitbucket_repo
    )

    # fetch issues from Bitbucket
    issues = get_issues(bb_url, options.start)

    # push them in GitHub (issues comments are fetched here)
    github_password = getpass.getpass("Please enter your GitHub password\n")
    github = Github(login=options.github_username, password=github_password)
    gh_username, gh_repository = options.github_repo.split('/')

    # Sort issues, to sync issue numbers on freshly created GitHub projects.
    # Note: not memory efficient, could use too much memory on large projects.
    for issue in sorted(issues, key=lambda issue: issue['local_id']):
        comments = get_comments(bb_url, issue)

        if options.dry_run:
            print "Title: {}".format(issue.get('title').encode('utf-8'))
            print "Body: {}".format(
                format_body(options, issue).encode('utf-8')
            )
            print "Comments", [comment['body'] for comment in comments]
        else:
            body = format_body(options, issue).encode('utf-8')
            push_issue(gh_username, gh_repository, issue, body, comments)
            print "Created {} issues".format(len(issues))
