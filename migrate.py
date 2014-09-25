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


from optparse import OptionParser
import urllib2
import getpass

from pygithub3 import Github

try:
    import json
except ImportError:
    import simplejson as json

BBURL = "https://api.bitbucket.org/1.0"


# Formatters
def format_user(author_info):
    name = "Anonymous"
    if not author_info:
        return name
    if 'first_name' in author_info and 'last_name' in author_info:
        name = " ".join([author_info['first_name'], author_info['last_name']])
    elif 'username' in author_info:
        name = author_info['username']
    if 'username' in author_info:
        return '[{}](http://bitbucket.org/{})'.format(
            name, author_info['username']
        )
    else:
        return name


def format_name(issue):
    if 'reported_by' in issue:
        return format_user(issue['reported_by'])
    else:
        return "Anonymous"


def format_body(issue):
    content = clean_body(issue.get('content'))
    url = "https://bitbucket.org/{}/{}/issue/{}".format(
        options.bitbucket_username, options.bitbucket_repo, issue['local_id']
    )
    return content + """\n
---------------------------------------
- Bitbucket: {}
- Originally Reported By: {}
- Originally Created At: {}
""".format(url, format_name(issue), issue['created_on'])


def format_comment(comment):
    return comment['body'] + """\n
---------------------------------------
Original Comment By: {}
    """.format(comment['user'].encode('utf-8'))


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


def get_comments(issue):
    '''
    Fetch the comments for an issue
    '''
    url = "{}/repositories/{}/{}/issues/{}/comments/".format(
        BBURL,
        options.bitbucket_username,
        options.bitbucket_repo,
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


if __name__ == "__main__":
    parser = OptionParser()

    parser.add_option(
        "-t", "--dry-run",
        action="store_true", dest="dry_run", default=False,
        help="Perform a dry run and print eveything."
    )

    parser.add_option(
        "-g", "--github-username", dest="github_username",
        help="GitHub username"
    )

    parser.add_option(
        "-s", "--bitbucket_repo", dest="bitbucket_repo",
        help="Bitbucket repo to pull data from."
    )

    parser.add_option(
        "-u", "--bitbucket_username", dest="bitbucket_username",
        help="Bitbucket username"
    )

    parser.add_option(
        "-d", "--github_repo", dest="github_repo",
        help="GitHub to add issues to. Format: <username>/<repo name>"
    )

    parser.add_option(
        "-f", "--start", type="int", dest="start",
        help="Bitbucket issue id from which to start import"
    )

    (options, args) = parser.parse_args()

    # Login in to github and create object
    github_password = getpass.getpass("Please enter your github password\n")
    github = Github(login=options.github_username, password=github_password)

    issue_counts = 0
    issues = []
    while True:
        url = "{}/repositories/{}/{}/issues/?start={}".format(
            BBURL,
            options.bitbucket_username,
            options.bitbucket_repo,
            options.start - 1   # -1 because the start option is id-1
        )
        try:
            response = urllib2.urlopen(url)
        except urllib2.HTTPError as ex:
            raise ValueError(
                'Problem trying to connect to bitbucket ({url}): {ex} '
                'Hint: the bitbucket repository name is case-sensitive.'
                .format(url=url, ex=ex))

        result = json.loads(response.read())
        if not result['issues']:
            # Check to see if there is issues to process if not break out.
            break

        for issue in result['issues']:
            issues.append(issue)
            options.start += 1

    # Sort issues, to sync issue numbers on freshly created GitHub projects.
    # Note: not memory efficient, could use too much memory on large projects.
    for issue in sorted(issues, key=lambda issue: issue['local_id']):
        comments = get_comments(issue)

        if options.dry_run:
            print "Title: {0}".format(issue.get('title').encode('utf-8'))
            print "Body: {0}".format(format_body(issue).encode('utf-8'))
            print "Comments", [comment['body'] for comment in comments]
        else:
            # Create the issue
            issue_data = {'title': issue.get('title').encode('utf-8'),
                          'body': format_body(issue).encode('utf-8')}
            ni = github.issues.create(
                issue_data,
                options.github_repo.split('/')[0],
                options.github_repo.split('/')[1]
            )

            # Set the status and labels
            if issue.get('status') == 'resolved':
                github.issues.update(ni.number,
                                     {'state': 'closed'},
                                     user=options.github_repo.split('/')[0],
                                     repo=options.github_repo.split('/')[1])

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

            #github.issues.labels.add_to_issue(ni.number,
            #                                  issue['metadata']['kind'],
            #                                  user=options.github_username,
            #                                  repo=options.github_repo,
            #                                  )
            #sys.exit()

            #github.issues.labels.add_to_issue(ni.number,
            #                                  options.github_username,
            #                                  options.github_repo,
            #                                  ('import',))

            # Milestones

            # Add the comments
            comment_count = 0
            for comment in comments:
                github.issues.comments.create(
                    ni.number,
                    format_comment(comment),
                    options.github_repo.split('/')[0],
                    options.github_repo.split('/')[1]
                )
                comment_count += 1

            print u"Created: {0} with {1} comments".format(issue['title'], comment_count)
        issue_counts += 1

    print "Created {0} issues".format(issue_counts)
