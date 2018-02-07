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
import pprint
import re
import sys
import time

import getpass
import requests

try:
    import keyring
    assert keyring.get_keyring().priority
except (ImportError, AssertionError):
    # no suitable keyring is available, so mock the interface
    # to simulate no pw
    class keyring:
        get_password = staticmethod(lambda system, username: None)


def read_arguments():
    parser = argparse.ArgumentParser(
        description="A tool to migrate issues from Bitbucket to GitHub."
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
        "github_repo",
        help=(
            "GitHub repository to add issues to.\n"
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
        "-bu", "--bb-user", dest="bitbucket_username",
        help=(
            "Your Bitbucket username. This is only necessary when migrating "
            "private Bitbucket repositories."
        )
    )

    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="Perform a dry run and print everything."
    )

    parser.add_argument(
        "-f", "--skip", type=int, default=0,
        help=(
            "The number of Bitbucket issues to skip. Note that if Bitbucket "
            "issues were deleted, they are already automatically skipped."
        )
    )

    parser.add_argument(
        "-m", "--map-user", action="append", dest="_map_users", default=[],
        help=(
            "Override user mapping for usernames, for example "
            "`--map-user fk=fkrull`.  Can be specified multiple times."
        ),
    )

    return parser.parse_args()


def main(options):
    """Main entry point for the script."""
    bb_url = "https://api.bitbucket.org/1.0/repositories/{repo}/issues".format(
        repo=options.bitbucket_repo)
    options.bb_auth = None
    options.users = dict(user.split('=') for user in options._map_users)
    bb_repo_status = requests.head(bb_url).status_code
    if bb_repo_status == 404:
        raise RuntimeError(
            "Could not find a Bitbucket Issue Tracker at: {}\n"
            "Hint: the Bitbucket repository name is case-sensitive."
            .format(bb_url)
        )
    elif bb_repo_status == 403:  # Only need BB auth creds for private BB repos
        if not options.bitbucket_username:
            raise RuntimeError(
                """
                Trying to access a private Bitbucket repository, but no
                Bitbucket username was entered. Please rerun the script using
                the argument `--bb-user <username>` to pass in your Bitbucket
                username.
                """
            )
        kr_pass_bb = keyring.get_password('Bitbucket', options.bitbucket_username)
        bitbucket_password = kr_pass_bb or getpass.getpass(
            "Please enter your Bitbucket password.\n"
            "Note: If your Bitbucket account has two-factor authentication "
            "enabled, you must temporarily disable it until "
            "https://bitbucket.org/site/master/issues/11774/ is resolved.\n"
        )
        options.bb_auth = (options.bitbucket_username, bitbucket_password)
        # Verify BB creds work
        bb_creds_status = requests.head(bb_url, auth=options.bb_auth).status_code
        if bb_creds_status == 401:
            raise RuntimeError("Failed to login to Bitbucket.")
        elif bb_creds_status == 403:
            raise RuntimeError(
                "Bitbucket login succeeded, but user '{}' doesn't have "
                "permission to access the url: {}"
                .format(options.bitbucket_username, bb_url)
            )

    # Always need the GH pass so format_user() can verify links to GitHub user
    # profiles don't 404. Auth'ing necessary to get higher GH rate limits.
    kr_pass_gh = keyring.get_password('Github', options.github_username)
    github_password = kr_pass_gh or getpass.getpass(
        "Please enter your GitHub password.\n"
        "Note: If your GitHub account has authentication enabled, "
        "you must use a personal access token from "
        "https://github.com/settings/tokens in place of a password for this "
        "script.\n"
    )
    options.gh_auth = (options.github_username, github_password)
    # Verify GH creds work
    gh_repo_url = 'https://api.github.com/repos/' + options.github_repo
    gh_repo_status = requests.head(gh_repo_url, auth=options.gh_auth).status_code
    if gh_repo_status == 401:
        raise RuntimeError("Failed to login to GitHub.")
    elif gh_repo_status == 403:
        raise RuntimeError(
            "GitHub login succeeded, but user '{}' either doesn't have "
            "permission to access the repo at: {}\n"
            "or is over their GitHub API rate limit.\n"
            "You can read more about GitHub's API rate limiting policies here: "
            "https://developer.github.com/v3/#rate-limiting"
            .format(options.github_username, gh_repo_url)
        )
    elif gh_repo_status == 404:
        raise RuntimeError("Could not find a GitHub repo at: " + gh_repo_url)

    # GitHub's Import API currently requires a special header
    headers = {'Accept': 'application/vnd.github.golden-comet-preview+json'}
    gh_milestones = GithubMilestones(options.github_repo, options.gh_auth, headers)

    print("getting issues from bitbucket")
    issues = get_issues(bb_url, options.skip, options.bb_auth)
    print("done, loaded {} issues".format(len(issues)))

    fill_gaps(issues, options.skip)
    for index, issue in enumerate(issues):
        if isinstance(issue, DummyIssue):
            comments = []
        else:
            comments = get_issue_comments(issue['local_id'], bb_url, options.bb_auth)

        gh_issue = convert_issue(issue, options, gh_milestones)
        gh_comments = [
            convert_comment(c, options) for c in comments
            # Bitbucket status comments (assigned, version, etc. changes) are
            # not imported to minimize noise.
            # These BB status comments have no content
            if c['content']
        ]

        if options.dry_run:
            print("\nIssue: ", gh_issue)
            print("\nComments: ", gh_comments)
        else:
            push_respo = push_github_issue(
                gh_issue, gh_comments, options.github_repo, options.gh_auth, headers
            )
            # issue POSTed successfully, now verify the import finished before
            # continuing. Otherwise, we risk issue IDs not being sync'd between
            # Bitbucket and GitHub because GitHub processes the data in the
            # background, so IDs can be out of order if two issues are POSTed
            # and the latter finishes before the former. For example, if the
            # former had a bunch more comments to be processed.
            # https://github.com/jeffwidman/bitbucket-issue-migration/issues/45
            status_url = push_respo.json()['url']
            resp = verify_github_issue_import_finished(
                status_url, options.gh_auth, headers)

            # Verify GH & BB issue IDs match.
            # If this assertion fails, convert_links() will have incorrect
            # output.  This condition occurs when:
            # - the GH repository has pre-existing issues.
            # - the Bitbucket repository has gaps in the numbering.
            if resp:
                gh_issue_url = resp.json()['issue_url']
                gh_issue_id = int(gh_issue_url.split('/')[-1])
                assert gh_issue_id == issue['local_id']
        print("Completed {} of {} issues".format(index + 1, len(issues)))


class DummyIssue(dict):
    def __init__(self, num):
        self.update(
            local_id=num,
            #...
        )


def fill_gaps(issues, offset):
    """
    Fill gaps in the issues, assuming an initial offset.

    >>> issues = [
    ...     dict(local_id=2),
    ...     dict(local_id=4),
    ...     dict(local_id=7),
    ... ]
    >>> fill_gaps(issues, 0)
    >>> [issue['local_id'] for issue in issues]
    [1, 2, 3, 4, 5, 6, 7]

    >>> issues = [
    ...     dict(local_id=52),
    ...     dict(local_id=54),
    ... ]
    >>> fill_gaps(issues, 50)
    >>> [issue['local_id'] for issue in issues]
    [51, 52, 53, 54]
    """
    start = offset + 1
    num = start
    index = num - start
    while index < len(issues):
        if issues[index]['local_id'] > num:
            issues[index:index] = [DummyIssue(num)]
        num += 1
        index = num - start


def get_issues(bb_url, offset, bb_auth):
    """Fetch the issues from Bitbucket."""
    issues = []
    initial_offset = offset

    while True:  # keep fetching additional pages of issues until all processed
        respo = requests.get(
            bb_url, auth=bb_auth,
            params={'sort': 'local_id', 'start': offset, 'limit': 50}
        )
        if respo.status_code == 200:
            result = respo.json()
            # check to see if there are issues to process, if not break out.
            if not result['issues']:
                break
            issues += result['issues']
            # 'start' is the current list index of the issue, not the issue ID
            offset += len(result['issues'])
        else:
            raise RuntimeError(
                "Bitbucket returned an unexpected HTTP status code: {}"
                .format(respo.status_code)
            )

    # BB returns a 'count' param that is the total number of issues
    assert len(issues) == result['count'] - initial_offset
    return issues


def get_issue_comments(issue_id, bb_url, bb_auth):
    """Fetch the comments for the specified Bitbucket issue."""
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


def convert_issue(issue, options, gh_milestones):
    """
    Convert an issue schema from Bitbucket to GitHub's Issue Import API
    """
    # Bitbucket issues have an 'is_spam' field that Akismet sets true/false.
    # they still need to be imported so that issue IDs stay sync'd

    if isinstance(issue, DummyIssue):
        return dict(
            title="dummy issue",
            body="filler issue created by bitbucket_issue_migration",
            closed=True,
        )
    labels = [issue['priority']]
    for k, v in issue['metadata'].items():
        if k in ['component', 'kind', 'version'] and v is not None:
            # Commas are permitted in Bitbucket's components & versions, but
            # they cannot be in GitHub labels, so they must be removed.
            labels.append(v.replace(',', ''))

    is_closed = issue['status'] not in ('open', 'new', 'on hold')
    out = {
        'title': issue['title'],
        'body': format_issue_body(issue, options),
        'closed': is_closed,
        'created_at': convert_date(issue['utc_created_on']),
        'updated_at': convert_date(issue['utc_last_updated']),
        'labels': labels,
        ####
        # GitHub Import API supports assignee, but we can't use it because
        # our mapping of BB users to GH users isn't 100% accurate
        # 'assignee': "jonmagic",
    }

    if is_closed:
        out['closed_at'] = convert_date(issue['utc_last_updated'])

    # If there's a milestone for the issue, convert it to a Github
    # milestone number (creating it if necessary).
    milestone_title = issue['metadata'].get('milestone')
    if milestone_title:
        out['milestone'] = gh_milestones.ensure(milestone_title)

    return out


def convert_comment(comment, options):
    """
    Convert an issue comment from Bitbucket schema to GitHub's Issue Import API
    schema.
    """
    return {
        'created_at': convert_date(comment['utc_created_on']),
        'body': format_comment_body(comment, options),
    }


def format_issue_body(issue, options):
    content = convert_changesets(issue['content'])
    content = convert_creole_braces(content)
    content = convert_links(content, options)
    content = convert_users(content, options)
    return """Originally reported by: **{reporter}**

{sep}

{content}

{sep}
- Bitbucket: https://bitbucket.org/{repo}/issue/{id}
""".format(
        # anonymous issues are missing 'reported_by' key
        reporter=format_user(issue.get('reported_by', None), options),
        sep='-' * 40,
        content=content,
        repo=options.bitbucket_repo,
        id=issue['local_id'],
    )


def format_comment_body(comment, options):
    content = convert_changesets(comment['content'])
    content = convert_creole_braces(content)
    content = convert_links(content, options)
    content = convert_users(content, options)
    return """*Original comment by* **{author}**:

{sep}

{content}
""".format(
        author=format_user(comment['author_info'], options),
        sep='-' * 40,
        content=content
    )


def _gh_username(username, users, gh_auth):
    try:
        return users[username]
    except KeyError:
        pass

    # Verify GH user link doesn't 404. Unfortunately can't use
    # https://github.com/<name> because it might be an organization
    gh_user_url = 'https://api.github.com/users/' + username
    status_code = requests.head(gh_user_url, auth=gh_auth).status_code
    if status_code == 200:
        users[username] = username
        return username
    elif status_code == 404:
        users[username] = None
        return None
    elif status_code == 403:
        raise RuntimeError(
            "GitHub returned HTTP Status Code 403 Forbidden when accessing: {}."
            "\nThis may be due to rate limiting.\n"
            "You can read more about GitHub's API rate limiting policies here: "
            "https://developer.github.com/v3/#rate-limiting"
            .format(gh_user_url)
        )
    else:
        raise RuntimeError(
            "Failed to check GitHub User url: {} due to "
            "unexpected HTTP status code: {}"
            .format(gh_user_url, status_code)
        )


def format_user(user, options):
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
    bb_user = "Bitbucket: [{0}](https://bitbucket.org/{0})".format(user['username'])
    gh_username = _gh_username(user['username'], options.users, options.gh_auth)
    if gh_username is not None:
        gh_user = "GitHub: [{0}](https://github.com/{0})".format(gh_username)
    else:
        gh_user = "GitHub: Unknown"
    return (user['display_name'] + " (" + bb_user + ", " + gh_user + ")")


def convert_date(bb_date):
    """Convert the date from Bitbucket format to GitHub format."""
    # '2012-11-26 09:59:39+00:00'
    m = re.search(r'(\d\d\d\d-\d\d-\d\d) (\d\d:\d\d:\d\d)', bb_date)
    if m:
        return '{}T{}Z'.format(m.group(1), m.group(2))

    raise RuntimeError("Could not parse date: {}".format(bb_date))


def convert_changesets(content):
    """
    Remove changeset references like:

        → <<cset 22f3981d50c8>>'

    Since they point to mercurial changesets and there's no easy way to map them
    to git hashes, better to remove them altogether.
    """
    lines = content.splitlines()
    filtered_lines = [l for l in lines if not l.startswith("→ <<cset")]
    return "\n".join(filtered_lines)


def convert_creole_braces(content):
    """
    Convert Creole code blocks to Markdown formatting.

    Convert text wrapped in "{{{" and "}}}" to "`" for inline code and
    four-space indentation for multi-line code blocks.
    """
    lines = []
    in_block = False
    for line in content.splitlines():
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
    Convert absolute links to other issues related to this repository to
    relative links ("#<id>").
    """
    pattern = r'https://bitbucket.org/{repo}/issue/(\d+)'.format(
        repo=options.bitbucket_repo)
    return re.sub(pattern, r'#\1', content)


MENTION_RE = re.compile(r'(?:^|(?<=[^\w]))@[a-zA-Z0-9_-]+\b')


def convert_users(content, options):
    """
    Replace @mentions with users specified on the cli.
    """
    def replace_user(match):
        matched = match.group()[1:]
        return '@' + options.users.get(matched, matched)

    return MENTION_RE.sub(replace_user, content)


class GithubMilestones:
    """
    This class handles creation of Github milestones for a given
    repository.

    When instantiated, it loads any milestones that exist for the
    respository. Calling ensure() will cause a milestone with
    a given title to be created if it doesn't already exist. The
    Github number for the milestone is returned.
    """

    def __init__(self, repo, auth, headers):
        self.url = 'https://api.github.com/repos/{repo}/milestones'.format(repo=repo)
        self.session = requests.Session()
        self.session.auth = auth
        self.session.headers.update(headers)
        self.refresh()

    def refresh(self):
        self.title_to_number = self.load()

    def load(self):
        milestones = {}
        url = self.url + "?state=all"
        while url:
            respo = self.session.get(url)
            if respo.status_code != 200:
                raise RuntimeError(
                    "Failed to get milestones due to HTTP status code: {}".format(
                    respo.status_code))
            for m in respo.json():
                milestones[m['title']] = m['number']
            url = respo.links.get("next")
        return milestones

    def ensure(self, title):
        number = self.title_to_number.get(title)
        if number is None:
            number = self.create(title)
            self.title_to_number[title] = number
        return number

    def create(self, title):
        respo = self.session.post(self.url, json={"title": title})
        if respo.status_code != 201:
            raise RuntimeError(
                "Failed to get milestones due to HTTP status code: {}".format(
                respo.status_code))
        return respo.json()["number"]


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
    Check the status of a GitHub issue import.

    If the status is 'pending', it sleeps, then rechecks until the status is
    either 'imported' or 'failed'.
    """
    while True:  # keep checking until status is something other than 'pending'
        respo = requests.get(status_url, auth=auth, headers=headers)
        if respo.status_code in (403, 404):
            print(respo.status_code, "retrieving status URL", status_url)
            respo.status_code == 404 and print(
                "GitHub sometimes inexplicably returns a 404 for the "
                "check url for a single issue even when the issue "
                "imports successfully. For details, see #77."
            )
            pprint.pprint(respo.headers)
            return
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
