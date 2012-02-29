from github2.client import Github
from BeautifulSoup import BeautifulSoup
from html2text import html2text
from datetime import datetime, timedelta
import urllib2
import time

try:
    import json
except ImportError:
    import simplejson as json

from optparse import OptionParser
parser = OptionParser()

parser.add_option("-t", "--dry-run", action="store_true", dest="dry_run", default=False,
    help="Preform a dry run and print eveything.")

parser.add_option("-g", "--github-username", dest="github_username",
    help="GitHub username")

parser.add_option("-a", "--github-api-token", dest="github_api_token",
    help="GitHub api token to login with")

parser.add_option("-d", "--github_repo", dest="github_repo",
    help="GitHub to add issues to. Format: <username>/<repo name>")

parser.add_option("-s", "--bitbucket_repo", dest="bitbucket_repo",
    help="Bitbucket repo to pull data from.")

parser.add_option("-u", "--bitbucket_username", dest="bitbucket_username",
    help="Bitbucket username")

(options, args) = parser.parse_args()


# Formatters
def format_name(issue):
    if 'reported_by' in issue:
        name = "Anonymous"
        reported_by = issue['reported_by']
        if 'first_name' in reported_by and 'last_name' in reported_by:
            name = " ".join([ reported_by['first_name'],reported_by['last_name']])
        elif 'username' in reported_by:
            name = reported_by['username']
        if 'username' in reported_by:
            return '[%s](http://bitbucket.org/%s)' % (name, reported_by['username'])
        else:
            return name
    else:
        return "Anonymous"

def format_body(issue):
    content = clean_body(issue.get('content'))
    url = "https://bitbucket.org/%s/%s/issue/%s" % (options.bitbucket_username, options.bitbucket_repo, issue['local_id'])
    return content + """\n
---------------------------------------
- Bitbucket: %s
- Originally Reported By: %s
- Originally Created At: %s
""" % (url, format_name(issue), issue['created_on'])

def format_comment(comment):
    return comment['body'] + """\n
---------------------------------------
    Original Comment By: %s
    """ % (comment['user'].encode('utf-8'))

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

def scrape_comments(issue):
    # This is a hack since the current BitBucket api does not support pulling comments.
    url = "https://bitbucket.org/%s/%s/issue/%s" % (options.bitbucket_username, options.bitbucket_repo, issue['local_id'])
    content = urllib2.urlopen(url).read()
    bs = BeautifulSoup(content)
    comments = []
    for comment in bs.findAll('li', {'class': ' comment-content'}):
        body = comment.find('div', {'class' : 'issues-comment edit-comment'})
        if body:
            body = html2text(unicode(body))
        else:
            # This is not a comment it is a issue change
            body = html2text(unicode(comment.find('ul', {'class' : 'issue-changes'})))
        body = clean_body(body)
        user = 'Anonymous'
        try:
            user = comment.findAll('a')[1].getText()
        except IndexError:
            pass

        created_at = comment.find('time').get('datetime')
        number = int(comment.find('a', {"class" : "issues-comments-permalink"}).getText().replace("#", ""))

        comments.append({
            'user': user,
            'created_at' : created_at,
            'body' : body.encode('utf-8'),
            'number' : number
        })

    return comments

github_api_count = 0
start_date = datetime.now()
def increment_api_call():
    global github_api_count
    github_api_count += 1

    if github_api_count > 40:
       global start_date
       sleep_time = ((start_date + timedelta(minutes=1)) - datetime.now()).seconds + 5
       print "Waiting for", sleep_time
       time.sleep(sleep_time)
       start_date = datetime.now()
       github_api_count = 0

# Login in to github and create account object
github = Github(api_token=options.github_api_token, username=options.github_username)
start = 0
issue_counts = 0
issues = []
while True:
    url = "https://api.bitbucket.org/1.0/repositories/%s/%s/issues/?start=%d" % (options.bitbucket_username, options.bitbucket_repo, start)
    response = urllib2.urlopen(url)
    result = json.loads(response.read())
    if not result['issues']:
        # Check to see if there is issues to process if not break out.
        break

    for issue in result['issues']:
        issues.append(issue)
        start += 1

# Sort issues, to sync issue numbers on freshly created GitHub projects.
# Note: not memory efficient, could use too much memory on large projects.
for issue in sorted(issues, key=lambda issue: issue['local_id']):
    comments = scrape_comments(issue)
    if options.dry_run:
        print "Title:", issue.get('title')
        print "Body:", format_body(issue)
        print "Comments", [comment['body'] for comment in comments]
    else:
        increment_api_call()
        ni = github.issues.open(options.github_repo,
            body=format_body(issue).encode('utf-8'),
            title=issue.get('title').encode('utf-8'),
        )

        increment_api_call()
        github.issues.add_label(options.github_repo, ni.number, issue['metadata']['kind'])

        increment_api_call()
        github.issues.add_label(options.github_repo, ni.number, "import")

        comment_count = 0
        for comment in comments:
            increment_api_call()
            github.issues.comment(options.github_repo, ni.number, format_comment(comment))
            comment_count += 1

        print "Created:", issue['title'], "With", comment_count, "comments"
    issue_counts += 1

print "Created", issue_counts, "Issues"
