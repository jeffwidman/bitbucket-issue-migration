# bitbucket Issues Migration

This is a small script that will migrate bitbucket issues to a github project.
It will use the bitbucket api to pull out the issues and comments.

It will import issues (and close them as needed) and their comments. Labels and
milestones are not supported at the moment.

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
      -d GITHUB_REPO, --github_repo=GITHUB_REPO
                            GitHub to add issues to. Format: <username>/<repo
                            name>
      -s BITBUCKET_REPO, --bitbucket_repo=BITBUCKET_REPO
                            Bitbucket repo to pull data from.
      -u BITBUCKET_USERNAME, --bitbucket_username=BITBUCKET_USERNAME
                            Bitbucket username
      -f START, --start=START
                            Bitbucket id of the issue to start import (1 means you want all the issues)                       

    python migrate.py -g <githbu_user> -d <github_repo> -s <bitbucket_repo> -u <bitbucket_usename> -f 1

Note: if you need to migrate to a GitHub organizational repository, use your personal username,
but the appropriate API token for the repository.
