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
    usage: migrate.py [-h] [-n] [-f START]
                      bitbucket_username bitbucket_repo github_username
                      github_repo

    A tool to migrate issues from Bitbucket to GitHub. note: the Bitbucket
    repository and issue tracker have to bepublic

    positional arguments:
      bitbucket_username    Your Bitbucket username
      bitbucket_repo        Bitbucket repository to pull data from.
      github_username       Your GitHub username
      github_repo           GitHub to add issues to. Format: <username>/<repo
                            name>

    optional arguments:
      -h, --help            show this help message and exit
      -n, --dry-run         Perform a dry run and print eveything.
      -f START, --start_id START
                            Bitbucket issue id from which to start import                  

    python migrate.py -f 1 <bitbucket_usename> <bitbucket_repo> <githbu_user>

Note: if you need to migrate to a GitHub organizational repository, use your personal username,
but the appropriate API token for the repository.
