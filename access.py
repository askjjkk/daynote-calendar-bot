class AccessPolicy(set):
    """Public mode accepts private-user IDs; chat ownership is checked by Bot.handle."""
    def __init__(self, ids=(), public=False):
        super().__init__(ids)
        self.public = public

    def __contains__(self, owner):
        return (isinstance(owner, int) and not isinstance(owner, bool) and owner > 0) if self.public else super().__contains__(owner)
