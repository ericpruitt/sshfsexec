#!/usr/bin/env python
# This is a sample configuration for sshfsexec.

# This part of the configuration is processed before any of the arguments are
# translated. The variable `sshremote` will not yet be determined (read: set to
# `None`) if the command was not executed within an SSHFS mount, and the
# transargs variable will not yet be defined.
if pre_process_config:
    translate_all_arguments = True

# This part of the configuration file is processed after translating the
# arguments to remote paths as needed.
else:
    # Parse out the username and host from sshremote.
    if sshremote and '@' in sshremote:
        user, server = sshremote.split('@')
    else:
        user = None
        server = sshremote

    # Ensure daemon / service control commands are run as root
    if command == 'service':
        sshremote = 'root@%s' % server

    # The server with the hostname "build-slave" should be used by make to do
    # everything remotely except for installation; the finished product gets
    # installed locally, but compilation takes place remotely.
    elif command == 'make' and server == 'build-slave':
        if 'install' in originalargs:
            # Set sshremote to None to execute "make install" locally.
            sshremote = None

    # CentOS 5 does not have a terminfo file for screen-256color, so set the
    # TERM environment variable to "screen" for the servers running CentOS 5.
    centos5 = ('example.com', 'server.tld', 'router.lan')
    if environment.get('TERM') == 'screen-256color' and server in centos5:
        environment['TERM'] = 'screen'
