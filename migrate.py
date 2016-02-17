# -*- coding: utf-8 -*-

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
import logging
import pprint
import re
import requests
import sys
import time
import zipfile
from _schema import MigrationInfo, Issue, Comment, User
from axiom.store import Store

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
        description = "A tool to migrate issues from Bitbucket to GitHub."
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
        "issues_zip",
        help="A zip file with an issues export from BitBucket."
    )

    parser.add_argument(
        "-n", "--dry-run", action="store_true", dest="dry_run", default=False,
        help="Perform a dry run and print everything."
    )

    parser.add_argument(
        "-d", "--debug", action="store_true", dest="debug", default=False,
        help="Turn on debug logging."
    )

    parser.add_argument(
        "--db", default="migration.axiom",
        help="Location to store the migration data."
    )

    return parser.parse_args()

def load_issues(store, options):
    with zipfile.ZipFile(options.issues_zip) as issues_zip:
        signature = issues_zip.read('.signature').decode('ascii')
        info = store.findUnique(MigrationInfo, default=None)
        if info is None:
            MigrationInfo(store=store, signature=signature)
        else:
            if signature == info.signature:
                return
            else:
                raise RuntimeError('Unable to resume: signature does not match')
        with issues_zip.open('db-1.0.json') as db_file:
            issues = json.load(db_file)
    for issue in issues[u'issues']:
        labels = [issue['priority']]
        for k in ['component', 'kind', 'milestone', 'version']:
            v = issue.get(k)
            if v is not None:
                labels.append(v)
        Issue(
            store=store,
            bitbucket_id=issue[u'id'],
            title=issue[u'title'],
            body=issue[u'content'],
            closed=issue[u'status'] not in (u'open', u'new'),
            created_at=convert_date(issue[u'created_on']),
            labels=labels,
        ).original_issue = issue
    for n, issue in enumerate(store.query(Issue, sort=Issue.bitbucket_id.asc), 1):
        issue.github_id = n
    for issue in store.query(Issue):
        issue.body = format_issue_body(issue, options, store)
    for comment in issues[u'comments']:
        if comment[u'content']:
            issue = store.findUnique(
                Issue, Issue.bitbucket_id == comment[u'issue'])
            Comment(
                store=store,
                issue=issue,
                created_at=convert_date(comment[u'created_on']),
                body=format_comment_body(comment, options, store),
            )

def main(options):
    """
    Main entry point for the script.
    """
    if options.debug:
        logging.basicConfig(level=logging.DEBUG)
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

    store = Store(options.db)
    store.transact(load_issues, store, options)

    for issue in store.query(Issue, Issue.in_progress == True):
        def tx():
            exists = check_issue_exists(
                options.github_repo, issue.github_id, options.gh_auth)
            issue.in_progress = False
            issue.migrated = exists
        store.transact(tx)

    total = store.query(Issue).count()
    issues = list(store.query(
        Issue, Issue.migrated == False, sort=Issue.github_id.asc))
    for issue in issues:
        issue.in_progress = True
        store.transact(import_issue, issue)
        print("Completed {} of {} issues".format(issue.github_id, total))


def format_issue_body(issue, options, store):
    content = convert_changesets(issue.original_issue[u'content'])
    content = convert_creole_braces(content)
    content = convert_links(content, options, store)
    return u"""Originally reported by: **{reporter}**

{sep}

{content}

{sep}
- Bitbucket: https://bitbucket.org/{repo}/issue/{id}
""".format(
        # anonymous issues are missing 'reported_by' key
        reporter=format_user(
            issue.original_issue.get(u'reporter', None),
            options.gh_auth,
            store),
        sep=u'-' * 40,
        content=content,
        repo=options.bitbucket_repo,
        id=issue.bitbucket_id,
    )


def format_comment_body(comment, options, store):
    content = convert_changesets(comment[u'content'])
    content = convert_creole_braces(content)
    content = convert_links(content, options, store)
    return u"""*Original comment by* **{author}**:

{sep}

{content}
""".format(
        author=format_user(comment[u'user'], options.gh_auth, store),
        sep='-' * 40,
        content=content
    )


def format_user(user, gh_auth, store):
    """
    Format a Bitbucket user's info into a string containing either 'Anonymous'
    or their name and links to their Bitbucket and GitHub profiles.
    The GitHub profile link may be incorrect because it assumes they reused
    their Bitbucket username on GitHub.
    """
    # anonymous comments have null 'author_info', anonymous issues don't have
    # 'reported_by' key, so just be sure to pass in None
    if user is None:
        return u"Anonymous"
    u = store.findUnique(User, User.user == user, None)
    if u is not None:
        return u.name
    bb_user = u"Bitbucket: [{0}](http://bitbucket.org/{0})".format(user)
    # Verify GH user link doesn't 404. Unfortunately can't use
    # https://github.com/<name> because it might be an organization
    gh_user_url = (u'https://api.github.com/users/' + user)
    status_code = requests.head(gh_user_url, auth=gh_auth).status_code
    if status_code == 200:
        gh_user = u"GitHub: [{0}](http://github.com/{0})".format(user)
    elif status_code == 404:
        gh_user = u"GitHub: Unknown"
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
    name = user + u" (" + bb_user + u", " + gh_user + u")"
    User(store=store, user=user, name=name)
    return name


def convert_date(bb_date):
    """
    Convert the date from Bitbucket format to GitHub format
    """
    # '2016-02-15T18:09:50.343889+00:00'
    m = re.search(ur'(\d\d\d\d-\d\d-\d\d)T(\d\d:\d\d:\d\d)', bb_date)
    if m:
        return u'{}T{}Z'.format(m.group(1), m.group(2))

    raise RuntimeError("Could not parse date: {}".format(bb_date))


def convert_changesets(content):
    """
    Remove changeset references like:

        → <<cset 22f3981d50c8>>'

    Since they point to mercurial changesets and there's no easy way to map them
    to git hashes, better to remove them altogether.
    """
    lines = content.splitlines()
    filtered_lines = [l for l in lines if not l.startswith(u"→ <<cset")]
    return u"\n".join(filtered_lines)


def convert_creole_braces(content):
    """
    Convert Creole code blocks that are wrapped in "{{{" and "}}}" to standard
    Markdown code formatting using "`" for inline code and four-space
    indentation for code blocks.
    """
    lines = []
    in_block = False
    for line in content.splitlines():
        if line.startswith(u"{{{") or line.startswith(u"}}}"):
            if u"{{{" in line:
                _, _, after = line.partition(u"{{{")
                lines.append(u'    ' + after)
                in_block = True
            if u"}}}" in line:
                before, _, _ = line.partition(u"}}}")
                lines.append(u'    ' + before)
                in_block = False
        else:
            if in_block:
                lines.append(u"    " + line)
            else:
                lines.append(line.replace(u"{{{", u"`").replace(u"}}}", u"`"))
    return u"\n".join(lines)


def convert_links(content, options, store):
    """
    Convert explicit links found in the body of a comment or issue to use
    relative links ("#<id>").
    """
    def map_pr(match):
        return u'pull request [#{bitbucket_id}](https://bitbucket.org/{repo}/pull-requests/{bitbucket_id})'.format(
            bitbucket_id=match.group(u'bitbucket_id'),
            repo=options.bitbucket_repo)
    content = re.sub(ur'pull request #(?P<bitbucket_id>\d+)', map_pr, content)
    def map_id(match):
        bitbucket_id = int(match.group(u'bitbucket_id'))
        issue = store.findUnique(
            Issue, Issue.bitbucket_id == bitbucket_id, None)
        #print match, bitbucket_id, issue
        if issue is None:
            github_id = bitbucket_id
        else:
            github_id = issue.github_id
        return u'#{}'.format(github_id)
    return re.sub(ur'#(?P<bitbucket_id>\d+)', map_id, content)


def push_github_issue(issue_data, github_repo, auth, headers):
    """
    Push a single issue to GitHub.

    Importing via GitHub's normal Issue API quickly triggers anti-abuse rate
    limits. So we use their dedicated Issue Import API instead:
    https://gist.github.com/jonmagic/5282384165e0f86ef105
    https://github.com/nicoddemus/bitbucket_issue_migration/issues/1
    """
    url = 'https://api.github.com/repos/{repo}/import/issues'.format(
        repo=github_repo)
    respo = requests.post(url, json=issue_data, auth=auth, headers=headers)
    if respo.status_code == 202:
        return respo
    elif respo.status_code == 422:
        raise RuntimeError(
            "Initial import validation failed for issue '{}' due to the "
            "following errors:\n{}".format(
                issue_data[u'issue'][u'title'], respo.json())
        )
    else:
        raise RuntimeError(
            "Failed to POST issue: '{}' due to unexpected HTTP status code: {}"
            .format(issue_data[u'issue'][u'title'], respo.status_code)
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
        status = respo.json()[u'status']
        if status != u'pending':
            break
        time.sleep(1)
    if status == u'imported':
        print("Imported Issue:", respo.json()[u'issue_url'])
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


def check_issue_exists(github_repo, github_id, auth):
    """
    If we were interrupted in the middle of an import, we need to check if the
    issue exists or not.  Not 100% reliable since it might still be importing
    if we resume too quickly.
    """
    url = 'https://api.github.com/repos/{repo}/issues/{id}'.format(
        repo=github_repo, id=github_id)
    res = requests.head(url, auth=auth)
    if res.status_code == 200:
        return True
    elif res.status_code == 404:
        return False
    else:
        raise RuntimeError(
            "Failed to check existence of GitHub issue, url: {} due to "
            "unexpected HTTP status code: {}"
            .format(url, res.status_code)
        )


def import_issue(issue):
    if options.dry_run:
        print("\nIssue: ")
        pprint.pprint(issue.json())
    else:
        # GitHub's Import API currently requires a special header
        headers = {'Accept': 'application/vnd.github.golden-comet-preview+json'}
        push_respo = push_github_issue(
            issue.json(), options.github_repo, options.gh_auth, headers
        )
        # issue POSTed successfully, now verify the import finished before
        # continuing. Otherwise, we risk issue IDs not being sync'd between
        # Bitbucket and GitHub because GitHub processes the data in the
        # background, so IDs can be out of order if two issues are POSTed
        # and the latter finishes before the former. For example, if the
        # former had a bunch more comments to be processed.
        # https://github.com/jeffwidman/bitbucket-issue-migration/issues/45
        status_url = push_respo.json()['url']
        verify_github_issue_import_finished(
            status_url, options.gh_auth, headers
        )
        issue.in_progress = False
        issue.migrated = True


if __name__ == "__main__":
    options = read_arguments()
    sys.exit(main(options))
