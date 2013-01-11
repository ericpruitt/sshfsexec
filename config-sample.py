#!/usr/bin/env python
# This is a sample configuration for sshfsexec.

# This part of the configuration is processed before any of the arguments are
# translated. The variable `sshlogin` will not yet be determined (read: set to
# `None`) if the command was not executed within an SSHFS mount, and the
# transargs variable will not yet be defined.
if pre_process_config:
    translate_all_arguments = True
    coerce_remote_execution = True

# This part of the configuration file is processed after translating the
# arguments to remote paths as needed.
else:
    # Parse out the username and host from sshlogin.
    if sshlogin and '@' in sshlogin:
        user, server = sshlogin.split('@')
    else:
        user = None
        server = sshlogin

    # Ensure daemon / service control commands are run as root
    if command == 'service':
        sshlogin = 'root@%s' % server

    # If stdin is a pipe, then force grep and sed to run locally since running
    # them remotely with piped data would only slow things down.
    elif command in ('grep', 'egrep', 'fgrep', 'sed') and stdin_is_pipe:
        sshlogin = None

    # ls(1) and grep(1) use isatty on stdout to determine whether or not to
    # display colors. To make `ls --color=auto` and `grep --color` display
    # colors at the end of a pipe series, preserve_isatty must be set since
    # sshfsexec would otherwise only allocate a TTY if both stdin and stdout
    # where TTY's.
    elif command == 'ls':
        preserve_isatty = True

    # The server with the hostname "build-slave" should be used by make to do
    # everything remotely except for installation; the finished product gets
    # installed locally, but compilation takes place remotely.
    elif command == 'make' and server == 'build-slave':
        if 'install' in originalargs:
            # Set sshlogin to None to execute "make install" locally.
            sshlogin = None

    # CentOS 5 does not have a terminfo file for screen-256color, so set the
    # TERM environment variable to "screen" for the servers running CentOS 5.
    centos5 = ('example.com', 'server.tld', 'router.lan')
    if environment.get('TERM') == 'screen-256color' and server in centos5:
        environment['TERM'] = 'screen'
