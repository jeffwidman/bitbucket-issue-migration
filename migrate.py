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
import time

try:
    import keyring
except ImportError:
    # keyring isn't available, so mock the interface to simulate no pw
    class keyring:
        get_password = staticmethod(lambda system, username: None)


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
        help=(
            "Your Bitbucket username. This is used only for authentication, "
            "not for the repository location."
        )
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
        help=(
            "Your GitHub username. This is used only for authentication, not "
            "for the repository location."
        )
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
        help="Perform a dry run and print everything."
    )

    parser.add_argument(
        "-f", "--start", type=int, dest="start", default=0,
        help=(
            "The list index of the Bitbucket issue from which to start the "
            "import. Note: Normally this matches the issue ID minus one "
            "(to account for zero-based indexing). However, if issues were "
            "deleted in the past from the BB repo, the list index of the issue "
            "will decrease due to the missing issues without a corresponding "
            "decrease in the issue ID."
        )
    )

    return parser.parse_args()

def main(options):
    """
    Main entry point for the script.
    """
    bb_url = "https://api.bitbucket.org/1.0/repositories/{repo}/issues".format(
        repo=options.bitbucket_repo)

    # resolve password upfront so the user isn't prompted later
    kr_pass_bb = keyring.get_password('Bitbucket', options.bitbucket_username)
    bitbucket_password = kr_pass_bb or getpass.getpass(
        "Please enter your Bitbucket password.\n"
    )
    kr_pass = keyring.get_password('Github', options.github_username)
    github_password = kr_pass or getpass.getpass(
        "Please enter your GitHub password.\n"
        "Note: If your account has two-factor authentication enabled, you must "
        "use a personal access token from https://github.com/settings/tokens "
        "in place of a password for this script.\n"
    )
    bb_auth = (options.bitbucket_username, bitbucket_password)
    gh_auth = (options.github_username, github_password)
    issues = get_issues(bb_url, options.start, bb_auth)

    for index, issue in enumerate(issues):
        comments = get_issue_comments(issue['local_id'], bb_url, bb_auth)
        gh_issue = convert_issue(issue, options)
        gh_comments = [convert_comment(c, options) for c in comments
                                if convert_comment(c, options) is not None]

        if options.dry_run:
            print("\nIssue: ", gh_issue)
            print("\nComments: ", gh_comments)
        else:
            # GitHub's Import API currently requires a special header
            headers = {'Accept': 'application/vnd.github.golden-comet-preview+json'}
            push_respo = push_github_issue(
                gh_issue, gh_comments, options.github_repo, gh_auth, headers
            )
            # issue POSTed successfully, now verify the import finished before
            # continuing. Otherwise, we risk issue IDs not being sync'd between
            # Bitbucket and GitHub because GitHub processes the data in the
            # background, so IDs can be out of order if two issues are POSTed
            # and the latter finishes before the former. For example, if the
            # former had a bunch more comments to be processed.
            # https://github.com/jeffwidman/bitbucket-issue-migration/issues/45
            status_url = push_respo.json()['url']
            gh_issue_url = verify_github_issue_import_finished(
                status_url, gh_auth, headers
                ).json()['issue_url']
            # verify GH & BB issue IDs match
            # if this fails, convert_links() will have incorrect output
            # this will fail if the GH repository has pre-existing issues
            gh_issue_id = int(gh_issue_url.split('/')[-1])
            assert gh_issue_id == issue['local_id']
        print("Completed {} of {} issues".format(index + 1, len(issues)))


def get_issues(bb_url, start, bb_auth):
    """
    Fetch the issues from Bitbucket
    """
    issues = []

    while True: # keep fetching additional pages of issues until all processed
        respo = requests.get(
                    bb_url, auth=bb_auth,
                    params={'sort': 'local_id', 'start': start, 'limit': 50})
        if respo.status_code == 200:
            result = respo.json()
            # check to see if there are issues to process, if not break out.
            if not result['issues']:
                break
            issues += result['issues']
            # 'start' is the current list index of the issue, not the issue ID
            start += len(result['issues'])

        elif respo.status_code == 401:
            raise RuntimeError(
                "Failed to login to Bitbucket."
                "Hint: You must disable two-factor authentication on your "
                "Bitbucket account until "
                "https://bitbucket.org/site/master/issues/11774/ is resolved"
            )
        elif respo.status_code == 404:
            raise RuntimeError(
                "Could not find the Bitbucket repository: {}\n"
                "Hint: the Bitbucket repository name is case-sensitive."
                .format(bb_url)
            )
        else:
            raise RuntimeError(
                "Bitbucket returned an unexpected HTTP status code: {}"
                .format(respo.status_code)
            )
    # BB returns a 'count' param that is the total number of issues
    assert len(issues) == result['count']
    return issues


def get_issue_comments(issue_id, bb_url, bb_auth):
    """
    Fetch the comments for the specified Bitbucket issue
    """
    url = "{bb_url}/{issue_id}/comments/".format(**locals())
    # BB API always returns newest comments first, regardless of 'sort' param;
    # however, comment order doesn't matter because we don't care about
    # comment IDs and GitHub sorts by creation date when displaying.
    respo = requests.get(url, auth=bb_auth)
    if respo.status_code != 200:
        raise RuntimeError(
            "Failed to get issue comments from: {} due to unexpected HTTP "
            "status code: {}"
            .format(url, respo.status_code)
        )
    return respo.json()


def convert_issue(issue, options):
    """
    Convert an issue schema from Bitbucket to GitHub's Issue Import API
    """
    # Bitbucket issues have an 'is_spam' field that Akismet sets true/false.
    # they still need to be imported so that issue IDs stay sync'd

    labels = [issue['priority']]
    for k, v in issue['metadata'].items():
        if k in ['component', 'kind', 'milestone', 'version'] and v is not None:
            labels.append(v)

    return {
        'title': issue['title'],
        'body': format_issue_body(issue, options),
        'closed': issue['status'] not in ('open', 'new'),
        'created_at': convert_date(issue['utc_created_on']),
        'labels': labels,
        # milestones are supported by both BB and GH APIs. Need to provide a
        # mapping from milestone titles in BB to milestone IDs in GH. The
        # milestone ID must already exist in GH or the import will be rejected.
        # GitHub schema: 'milestone': <integer ID>
        # Bitbucket schema: issue['metadata']['milestone']: <string Title>
        ####
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
            'created_at': convert_date(comment['utc_created_on']),
            'body': format_comment_body(comment, options),
        }


def format_issue_body(issue, options):
    content = convert_changesets(issue['content'])
    content = convert_creole_braces(content)
    content = convert_links(content, options)
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


def format_comment_body(comment, options):
    content = convert_changesets(comment['content'])
    content = convert_creole_braces(content)
    content = convert_links(content, options)
    return """*Original comment by* **{author}**:

{sep}

{content}
""".format(
        author=format_user(comment['author_info']),
        sep='-' * 40,
        content=content
    )


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
    return (
        user['display_name'] + " (Bitbucket: [{0}](http://bitbucket.org/{0}), "
        "GitHub: [{0}](http://github.com/{0}))"
        .format(user['username'])
    )


def convert_date(bb_date):
    """
    Convert the date from Bitbucket format to GitHub format
    """
    # '2012-11-26 09:59:39+00:00'
    m = re.search(r'(\d\d\d\d-\d\d-\d\d) (\d\d:\d\d:\d\d)', bb_date)
    if m:
        return '{}T{}Z'.format(m.group(1), m.group(2))

    raise RuntimeError("Could not parse date: {}".format(bb_date))


def convert_changesets(body):
    """
    Remove changeset references like:

        → <<cset 22f3981d50c8>>'

    Since they point to mercurial changesets and there's no easy way to map them
    to git hashes, better to remove them altogether.
    """
    lines = body.splitlines()
    filtered_lines = [l for l in lines if not l.startswith("→ <<cset")]
    return "\n".join(filtered_lines)


def convert_creole_braces(body):
    """
    Convert Creole code blocks that are wrapped in "{{{" and "}}}" to standard
    Markdown code formatting using "`" for inline code and four-space
    indentation for code blocks.
    """
    lines = []
    in_block = False
    for line in body.splitlines():
        if line.startswith("{{{") or line.startswith("}}}"):
            if "{{{" in line:
                _, _, after = line.partition("{{{")
                lines.append('    ' + after)
                in_block = True
            if "}}}" in line:
                before, _, _ = line.partition("}}}")
                lines.append('    ' + before)
                in_block = False
        else:
            if in_block:
                lines.append("    " + line)
            else:
                lines.append(line.replace("{{{", "`").replace("}}}", "`"))
    return "\n".join(lines)


def convert_links(content, options):
    """
    Convert explicit links found in the body of a comment or issue to use
    relative links ("#<id>").
    """
    pattern = r'https://bitbucket.org/{repo}/issue/(\d+)'.format(
            repo=options.bitbucket_repo)
    return re.sub(pattern, r'#\1', content)


def push_github_issue(issue, comments, github_repo, auth, headers):
    """
    Push a single issue to GitHub.

    Importing via GitHub's normal Issue API quickly triggers anti-abuse rate
    limits. So we use their dedicated Issue Import API instead:
    https://gist.github.com/jonmagic/5282384165e0f86ef105
    https://github.com/nicoddemus/bitbucket_issue_migration/issues/1
    """
    issue_data = {'issue': issue, 'comments': comments}
    url = 'https://api.github.com/repos/{repo}/import/issues'.format(
            repo=github_repo)
    respo = requests.post(url, json=issue_data, auth=auth, headers=headers)
    if respo.status_code == 202:
        return respo
    elif respo.status_code == 401:
        raise RuntimeError(
            "Failed to login to GitHub. If your account has two-factor "
            "authentication enabled, you must use a personal access token from "
            "https://github.com/settings/tokens in place of a password for "
            "this script.\n"
        )
    elif respo.status_code == 422:
        raise RuntimeError(
            "Initial import validation failed for issue '{}' due to the "
            "following errors:\n{}".format(issue['title'], respo.json())
        )
    else:
        raise RuntimeError(
            "Failed to POST issue: '{}' due to unexpected HTTP status code: {}"
            .format(issue['title'], respo.status_code)
        )


def verify_github_issue_import_finished(status_url, auth, headers):
    """
    Checks the status of a GitHub issue import. If the status is 'pending',
    it sleeps, then rechecks until the status is either 'imported' or 'failed'.
    """
    while True: # keep checking until status is something other than 'pending'
        respo = requests.get(status_url, auth=auth, headers=headers)
        if respo.status_code != 200:
            raise RuntimeError(
                "Failed to check GitHub issue import status url: {} due to "
                "unexpected HTTP status code: {}"
                .format(status_url, respo.status_code)
            )
        status = respo.json()['status']
        if status != 'pending':
            break
        time.sleep(1)
    if status == 'imported':
        print("Imported Issue:", respo.json()['issue_url'])
    elif status == 'failed':
        raise RuntimeError(
            "Failed to import GitHub issue due to the following errors:\n{}"
            .format(respo.json())
        )
    else:
        raise RuntimeError(
            "Status check for GitHub issue import returned unexpected status: "
            "'{}'"
            .format(status)
        )
    return respo


if __name__ == "__main__":
    options = read_arguments()
    sys.exit(main(options))
