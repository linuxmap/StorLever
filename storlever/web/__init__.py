def includeme(config):
    # look into following modules' includeme function
    # in order to register routes
    config.include(__name__ + '.index')
    config.include(__name__ + '.login')
    config.scan()    # scan to register view callables, must be last statement