FROM debian:testing

# Install dependencies
RUN \
  apt-get update && \
  apt-get install --no-install-recommends -y git python3-pip python3-setuptools python3-wheel && \
  apt-get clean && rm -rf /var/lib/apt/lists/*

# Install bitbucket-issue-migration
# keyring is not currently specified in requirements.pip since it's optional
RUN \
  pip3 install keyring && \
  git clone https://github.com/jeffwidman/bitbucket-issue-migration.git && \
  cd bitbucket-issue-migration && \
  pip3 install -r requirements.pip

# Configure entrypoint for executable container
ENTRYPOINT ["python3", "bitbucket-issue-migration/migrate.py"]
CMD ["-h"]
