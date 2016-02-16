from axiom import item, attributes


class MigrationInfo(item.Item):
    signature = attributes.text(allowNone=False)


class Issue(item.Item):
    bitbucket_id = attributes.integer(allowNone=False)
    github_id = attributes.integer(allowNone=True, default=None)
    migrated = attributes.boolean(allowNone=False, default=False)
    in_progress = attributes.boolean(allowNone=False, default=False)

    title = attributes.text(allowNone=False)
    body = attributes.text(allowNone=False)
    closed = attributes.boolean(allowNone=False)
    created_at = attributes.text(allowNone=False)
    labels = attributes.textlist(allowNone=False)

    original_issue = attributes.inmemory()

    def json(self):
        return {
            u'issue': {
                u'title': self.title,
                u'body': self.body,
                u'closed': self.closed,
                u'created_at': self.created_at,
                u'labels': self.labels,
            },
            u'comments': [
                comment.json()
                for comment
                in self.store.query(Comment, Comment.issue == self)
            ]
        }


class Comment(item.Item):
    issue = attributes.reference(allowNone=False, reftype=Issue)

    created_at = attributes.text(allowNone=False)
    body = attributes.text(allowNone=False)

    def json(self):
        return {
            u'created_at': self.created_at,
            u'body': self.body,
        }


class User(item.Item):
    user = attributes.text(allowNone=False)
    name = attributes.text(allowNone=False)
