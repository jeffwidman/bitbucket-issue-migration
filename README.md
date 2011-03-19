# bitbucket Issues Migration

This is a small script that will migrate bitbucket issues to a github project. It will use the bitbucket api
to pull out the issues and then scrape the screen to pull the comments out. The script will also throttle
the amount of requests it makes per minute to avoid the 60 request per minute limit that github enforces.

## Before running

You will need to install the requirements first

    pip install -r requirements.pip

## Example
    
    python migrate.py -h
    Usage: migrate.py [options]
    
    Options:
      -h, --help            show this help message and exit
      -t, --dry-run         Preform a dry run and print eveything.
      -g GITHUB_USERNAME, --github-username=GITHUB_USERNAME
                            GitHub username
      -a GITHUB_API_TOKEN, --github-api-token=GITHUB_API_TOKEN
                            GitHub api token to login with
      -d GITHUB_REPO, --github_repo=GITHUB_REPO
                            GitHub to add issues to. Format: <username>/<repo
                            name>
      -s BITBUCKET_REPO, --bitbucket_repo=BITBUCKET_REPO
                            Bitbucket repo to pull data from.
      -u BITBUCKET_USERNAME, --bitbucket_username=BITBUCKET_USERNAME
                            Bitbucket username

    python migrate.py -g <githbu_user> -a <github_api_token> -d <github_repo> -s <bitbucket_repo> -u <bitbucket_usename>

Note: if you need to migrate to a GitHub organizational repository, use your personal username, but the appropriate 
API token for the repository.
