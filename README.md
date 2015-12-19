# Bitbucket Issues Migration

This is a small script that will migrate Bitbucket issues to a GitHub project.

It will import issues (and close them as needed) and their comments. Labels are
supported.

## Before running:

You will need to install the requirements first

    pip install -r requirements.pip

## Example:

    python migrate.py -h
    usage: migrate.py [-h] [-n] [-f START]
                      bitbucket_username bitbucket_repo github_username
                      github_repo

    A tool to migrate issues from Bitbucket to GitHub.
    Note: The Bitbucket repository and issue tracker have to be public

    positional arguments:
      bitbucket_username    Your Bitbucket username
      bitbucket_repo        Bitbucket repository to pull data from.
      github_username       Your GitHub username
      github_repo           GitHub repository to add issues to.
                            Format: <user or organization name>/<repo name>
                            Example: jeffwidman/bitbucket_issue_migration

    optional arguments:
      -h, --help            show this help message and exit
      -n, --dry-run         Perform a dry run and print everything.
      -f START, --start_id START
                            Bitbucket issue ID from which to start the import

    python migrate.py -f 1 <bitbucket_username> <bitbucket_repo> <github_username> <github_repo>

## Additional notes:

* The Github repository can be owned by either an individual or an organization.

2. The maximum allowable size per individual issue is 1MB. This limit is
imposed by Github's
[Import API](https://gist.github.com/jonmagic/5282384165e0f86ef105).



Currently maintained by [Jeff Widman](http://www.jeffwidman.com/).
Originally written and open-sourced by [Vitaly Babiy](http://www.howsthe.com/).
