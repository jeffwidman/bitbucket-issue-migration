#!/usr/bin/env python

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
        help=(
            "Bitbucket repository to pull issues from.\n"
            "Format: <user or organization name>/<repo name>\n"
            "Example: jeffwidman/bitbucket-issue-migration"
        )
    )

    parser.add_argument(
        "github_username",
        help="Your GitHub username"
    )

    parser.add_argument(
        "github_repo",
        help=(
            "GitHub repository to add issues to.\n"
            "Format: <user or organization name>/<repo name>\n"
            "Example: jeffwidman/bitbucket-issue-migration"
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
        result = " ".join([author_info['first_name'], author_info['last_name']])

    if author_info and 'username' in author_info:
        # we assume they reused their Bitbucket username on Github
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
    content = clean_body(issue['content'])
    content = fix_links(options, content)
    return """Originally reported by: **{reporter}**

{sep}

{content}

{sep}
- Bitbucket: https://bitbucket.org/{repo}/issue/{id}
""".format(
        reporter=format_name(issue),
        sep='-' * 40,
        content=content,
        repo=options.bitbucket_repo,
        id=issue['local_id'],
    )


def format_comment(options, comment):
    return """*Original comment by* **{}**:

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
    pattern = r'https://bitbucket.org/{repo}/issue/(\d+)'.format(
            repo=options.bitbucket_repo)
    return re.sub(pattern, r'#\1', content)


def format_date(bb_date):
    """
    Convert from one of the various date formats used by Bitbucket to
    the one supported by GitHub.
    """
    # '2010-10-12T13:14:44.584'
    m = re.search(r'(\d\d\d\d-\d\d-\d\d)T(\d\d:\d\d:\d\d)', bb_date)
    if m:
        return '{}T{}Z'.format(m.group(1), m.group(2))
    # '2012-11-26 09:59:39+00:00'
    m = re.search(r'(\d\d\d\d-\d\d-\d\d) (\d\d:\d\d:\d\d)', bb_date)
    if m:
        return '{}T{}Z'.format(m.group(1), m.group(2))

    raise RuntimeError('Could not parse date: {}'.format(bb_date))


def clean_body(body):
    lines = []
    in_block = False
    for line in body.splitlines():
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
        if line.startswith("→ <<cset"):
            lines.pop(index)

def get_issues(bb_url, start_id):
    """
    Fetch the issues from Bitbucket
    """
    issues = []

    while True: # keep fetching additional pages of issues until all processed
        url = "{bb_url}/?start={start_id}".format(**locals())
        bb_issue_response = requests.get(url)

        if bb_issue_response.status_code in (200, 202):
            result = bb_issue_response.json()
            if not result['issues']:
                # Check to see if there are issues to process if not break out.
                break

            issues += result['issues']
            start_id += len(result['issues'])

        elif bb_issue_response.status_code == 404:
            raise RuntimeError(
                "Could not find the Bitbucket repository: {url}\n"
                "Hint: the Bitbucket repository name is case-sensitive."
                .format(url=url)
                )

        elif bb_issue_response.status_code == 401:
            raise RuntimeError(
                "Failed to login to Bitbucket."
                "Hint: You must disable two-factor authentication on your "
                "Bitbucket account until "
                "https://bitbucket.org/site/master/issues/11774/ is resolved"
                )

        else:
            raise RuntimeError(
                "Bitbucket returned an unexpected HTTP status code: {code}"
                .format(bb_issue_response.status_code)
                )

    return issues


def get_issue_comments(bb_url, issue):
    """
    Fetch the comments for a Bitbucket issue
    """
    url = "{bb_url}/{issue[local_id]}/comments/".format(**locals())
    result = requests.get(url).json()
    ordered = sorted(result, key=lambda comment: comment['utc_created_on'])

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

def print_issue(issue, comments, options):
    """
    Print the output of processing a single issue and associated comments
    """
    print("Title: {}".format(issue['title']))
    print("Body: {}".format(format_body(options, issue)))
    print("Comments", [format_comment(options, comment)
                                            for comment in comments])

def push_issue(auth, github_repo, issue, body, comments, options):
    """
    Push a single issue to Github
    """
    # Importing via Github's normal Issue API quickly triggers anti-abuse rate
    # limits. So we use the Issue Import API instead:
    # https://github.com/nicoddemus/bitbucket_issue_migration/issues/1
    # https://gist.github.com/jonmagic/5282384165e0f86ef105

    comments_data = [
        {
            'body': format_comment(options, x),
            'created_at': format_date(x['created_at']),
        } for x in comments]

    issue_data = {
        'issue': {
            'title': issue['title'],
            'body': body,
            'closed': issue['status'] not in ('open', 'new'),
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

    url = 'https://api.github.com/repos/{repo}/import/issues'.format(
        repo=github_repo)
    headers = {'Accept': 'application/vnd.github.golden-comet-preview+json'}
    respo = requests.post(url, json=issue_data, auth=auth, headers=headers)
    if respo.status_code in (200, 202):
        print("Created bitbucket issue {}: {} [{} comments]".format(
                            issue['local_id'], issue['title'], len(comments))
            )
    elif respo.status_code == 401:
        raise RuntimeError(
            "Failed to login to Github. If your account has two-factor "
            "authentication enabled, you must use a personal access token from "
            "https://github.com/settings/tokens in place of a password for "
            "this script.\n"
            )
    else:
        raise RuntimeError("Failed to create issue: {}".format(issue['local_id']))


if __name__ == "__main__":
    options = read_arguments()
    bb_url = "https://api.bitbucket.org/1.0/repositories/{repo}/issues".format(
        repo=options.bitbucket_repo)

    # ask for password so the user doesn't have to sit around waiting
    # to provide some initial input
    github_password = getpass.getpass(
        "Please enter your GitHub password.\n"
        "Note: If your account has two-factor authentication enabled, you must "
        "use a personal access token from https://github.com/settings/tokens "
        "in place of a password for this script.\n"
        )

    gh_auth = (options.github_username, github_password)

    issues = get_issues(bb_url, options.start)

    # sort issues, to sync issue numbers on freshly created GitHub projects.
    # Note: not memory efficient, could use too much memory on large projects.
    issues = sorted(issues, key=lambda issue: issue['local_id'])

    for index, issue in enumerate(issues):
        comments = get_issue_comments(bb_url, issue)

        if options.dry_run:
            print_issue(issue, comments, options)
            print("Dryrun: {} of {} issues".format(index + 1, len(issues)))
        else:
            body = format_body(options, issue)
            push_issue(gh_auth, options.github_repo, issue, body,
                       comments, options)
            print("Created {} of {} issues".format(index + 1, len(issues)))
