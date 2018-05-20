# Bitbucket Issues Migration

This is a small script that will migrate Bitbucket issues to a GitHub project.

It will import issues (and close them as needed) and their comments.
Repositories can be public or private, owned by individuals or organizations.
Labels and milestones are supported.

## Before running:

Requires Python 3 and the [`requests`](http://requests.readthedocs.org/) library.
[`keyring`](https://pypi.python.org/pypi/keyring) is an optional
dependency if you want to pull login credentials from the system keyring.

It's probably easiest to install the dependencies using Python 3's built-in
`venv` tool:

    $ pyvenv ./py3
    $ source ./py3/bin/activate
    $ pip3 install -r requirements.pip

## Parameters:

    $ python3 migrate.py -h
    usage: migrate.py [-h] [-bu BITBUCKET_USERNAME] [-n] [-f SKIP] [-m _MAP_USERS]
                      bitbucket_repo github_repo github_username

    A tool to migrate issues from Bitbucket to GitHub.

    positional arguments:
      bitbucket_repo        Bitbucket repository to pull issues from.
                            Format: <user or organization name>/<repo name>
                            Example: jeffwidman/bitbucket-issue-migration

      github_repo           GitHub repository to add issues to.
                            Format: <user or organization name>/<repo name>
                            Example: jeffwidman/bitbucket-issue-migration

      github_username       Your GitHub username. This is used only for
                            authentication, not for the repository location.

    optional arguments:
      -h, --help            show this help message and exit

      -bu BITBUCKET_USERNAME, --bb-user BITBUCKET_USERNAME
                            Your Bitbucket username. This is only necessary when
                            migrating private Bitbucket repositories.

      -n, --dry-run         Perform a dry run and print everything.

      -f SKIP, --skip SKIP  The number of Bitbucket issues to skip. Note that if
                            Bitbucket issues were deleted, they are already
                            automatically skipped.

      -m _MAP_USERS, --map-user _MAP_USERS
                            Override user mapping for usernames, for example
                            `--map-user fk=fkrull`. Can be specified multiple
                            times.

      --skip-attribution-for BB_SKIP
                            BitBucket user who doesn't need comments re-
                            attributed. Useful to skip your own comments, because
                            you are running this script, and the GitHub comments
                            will be already under your name.

    $ python3 migrate.py <bitbucket_repo> <github_repo> <github_username>

## Example:

For example, to export the SQLAlchemy issue tracker to the repo https://github.com/jeffwidman/testing:

    $ python3 migrate.py zzzeek/sqlalchemy jeffwidman/testing jeffwidman

## Additional notes:

* GitHub labels are created that map to the Bitbucket issue's priority, kind
(bug, task, etc), component (if any, custom to each project), and version (if
any). If you don't want these, just delete the new GitHub labels post-migration.

* Milestones are transferred. If the milestone doesn't exist in GitHub, it will
be created. If you don't want this, either edit the code (search for "milestone")
or delete the milestones in GitHub after the migration.

* The migrated issues and issue comments are annotated with both Bitbucket and
GitHub links to user who authored the comment/issue. This assumes the user
reused their Bitbucket username on GitHub.

* Within the body of issues and issue comments, hyperlinks to other issues
in this Bitbucket repo will be rewritten as `#<ID>`, which GitHub will
automatically hyperlink to the GitHub issue with that particular ID. This
assumes that you are migrating to a GitHub repository that has no existing
issues, otherwise the imported issues will have a different ID on GitHub than
on Bitbucket and the links will be incorrect. If you are migrating to a GitHub
repo with existing issues, just edit the code to offset the imported issue IDs
by the correct amount.

* This script is not idempotent--re-running it will leave the first set of
imported issues intact, and then create a duplicate set of imported issues after
the first set. If you want to re-run the import, it's best to delete your GitHub
repo and start over so that the GitHub issue IDs start from 1.

* The maximum allowable size per individual issue is 1MB. This limit is
imposed by GitHub's
[Import API](https://gist.github.com/jonmagic/5282384165e0f86ef105).



Currently maintained by [Jeff Widman](http://www.jeffwidman.com/).
Originally written and open-sourced by [Vitaly Babiy](https://github.com/vbabiy).
