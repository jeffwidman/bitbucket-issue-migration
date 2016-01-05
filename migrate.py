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
import sys


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
        "-f", "--start_id", type=int, dest="start_id", default=1,
        help="Bitbucket issue ID from which to start the import"
    )

    return parser.parse_args()

def main(options):
    """
    Main entry point for the script.
    """
    bb_url = "https://api.bitbucket.org/1.0/repositories/{repo}/issues".format(
        repo=options.bitbucket_repo)

    # ask for password upfront so the user doesn't have to sit around waiting
    github_password = getpass.getpass(
        "Please enter your GitHub password.\n"
        "Note: If your account has two-factor authentication enabled, you must "
        "use a personal access token from https://github.com/settings/tokens "
        "in place of a password for this script.\n"
        )
    gh_auth = (options.github_username, github_password)

    issues = get_issues(bb_url, options.start_id)
    for index, issue in enumerate(issues):
        comments = get_issue_comments(issue, bb_url)
        # issue can't be converted until comments are retrieved because need
        # access to issue['local_id'] for retrieving comments
        issue = convert_issue(issue, options)
        comments = [convert_comment(c, options) for c in comments
                                if convert_comment(c, options) is not None]

        if options.dry_run:
            print("\nIssue:", issue)
            print("\nComments: ", comments)
        else:
            push_issue(issue, comments, options.github_repo, gh_auth)
        print("Completed {} of {} issues".format(index + 1, len(issues)))


# Formatters
def format_user(user):
    """
    Format a Bitbucket user's info into a string containing either 'Anonymous'
    or their name and links to their Bitbucket and GitHub profiles.
    The GitHub profile link may be incorrect because it assumes they reused
    their Bitbucket username on GitHub.
    """
    # anonymous comments have null 'author_info', anonymous issues don't have
    # 'reported_by' key, so just be sure to pass in None
    if user is None:
        return "Anonymous"
    return (user['display_name'] + " (Bitbucket: [{0}]"
            "(http://bitbucket.org/{0}), GitHub: [{0}](http://github.com/{0}))"
            .format(user['username']))


def format_body(issue, options):
    content = clean_body(issue['content'])
    content = format_links(content, options)
    return """Originally reported by: **{reporter}**

{sep}

{content}

{sep}
- Bitbucket: https://bitbucket.org/{repo}/issue/{id}
""".format(
        # anonymous issues are missing 'reported_by' key
        reporter=format_user(issue.get('reported_by', None)),
        sep='-' * 40,
        content=content,
        repo=options.bitbucket_repo,
        id=issue['local_id'],
    )


def format_comment(comment, options):
    return """*Original comment by* **{author}**:

{sep}

{content}
""".format(
        author=format_user(comment['author_info']),
        sep='-' * 40,
        content=format_links(clean_comment(comment['content']), options),
    )


def format_links(content, options):
    """
    Convert explicit links found in the body of a comment or issue to use
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

    raise RuntimeError("Could not parse date: {}".format(bb_date))


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
    start_id -= 1 # BB API sorting uses 0-based indexing
    issues = []

    while True: # keep fetching additional pages of issues until all processed
        bb_issue_response = requests.get(bb_url,
                                params={'sort': 'local_id', 'start': start_id})
        if bb_issue_response.status_code in (200, 202):
            result = bb_issue_response.json()
            # check to see if there are issues to process, if not break out.
            if not result['issues']:
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


def get_issue_comments(issue, bb_url):
    """
    Fetch the comments for the specified Bitbucket issue
    """
    url = "{bb_url}/{issue[local_id]}/comments/".format(**locals())
    # BB API always returns newest comments first, regardless of 'sort' param;
    # however, comment order doesn't matter because we don't care about
    # comment IDs and GitHub orders by creation date when displaying.
    return requests.get(url).json()


def convert_issue(issue, options):
    """
    Convert an issue schema from Bitbucket to GitHub's Issue Import API
    """
    # Bitbucket issues have an 'is_spam' field that Akismet sets true/false.
    # they still need to be imported so that issue IDs stay sync'd

    labels = []
    if issue['metadata']['kind']:
        labels.append(issue['metadata']['kind'])
    if issue['metadata']['component']:
        labels.append(issue['metadata']['component'])

    return {
        'title': issue['title'],
        'body': format_body(issue, options),
        'closed': issue['status'] not in ('open', 'new'),
        'created_at': format_date(issue['created_on']),
        'labels': labels
        # GitHub Import API supports assignee, but we can't use it because
        # our mapping of BB users to GH users isn't 100% accurate
        # 'assignee': "jonmagic",
        }


def convert_comment(comment, options):
    """
    Convert an issue comment from Bitbucket schema to GitHub's Issue Import API
    schema. Bitbucket status comments (assigned, version, etc. changes) are not
    imported to minimize noise.
    """
    if comment['content']: # BB status comments have no content
        return {
            'created_at': format_date(comment['utc_created_on']),
            'body': format_comment(comment, options),
            }


def push_issue(issue, comments, github_repo, auth):
    """
    Push a single issue to GitHub via their Issue Import API
    """
    # Importing via GitHub's normal Issue API quickly triggers anti-abuse rate
    # limits. So we use their dedicated Issue Import API instead:
    # https://github.com/nicoddemus/bitbucket_issue_migration/issues/1
    # https://gist.github.com/jonmagic/5282384165e0f86ef105
    issue_data = {'issue': issue, 'comments': comments}
    url = 'https://api.github.com/repos/{repo}/import/issues'.format(
        repo=github_repo)
    headers = {'Accept': 'application/vnd.github.golden-comet-preview+json'}
    respo = requests.post(url, json=issue_data, auth=auth, headers=headers)
    if respo.status_code in (200, 202):
        print("Created Bitbucket issue: {} [{} comments]".format(
                                        issue['title'], len(comments)))
    elif respo.status_code == 401:
        raise RuntimeError(
            "Failed to login to GitHub. If your account has two-factor "
            "authentication enabled, you must use a personal access token from "
            "https://github.com/settings/tokens in place of a password for "
            "this script.\n"
            )
    else:
        raise RuntimeError("Failed to create issue: {}".format(issue['title']))


if __name__ == "__main__":
    options = read_arguments()
    sys.exit(main(options))
