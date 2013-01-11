sshfsexec
=========

Description
-----------

I use SSHFS on a daily basis so I can program on a remote development server
using local applications. While the access times are generally acceptable on a
modern broadband connection for basic editing with Vim, attempting to run
something like git or grepping multiple files tends to be insufferably slow. To
resolve this, I wrote sshfsexec which transparently executes commands on remote
systems that host locally mounted SSHFS volumes.

Basic Configuration and Usage
-----------------------------

Copy sshfsexec to a folder defined in your `PATH` environment variable and make
sure the file is executable. The folder that sshfsexec is copied to should not
contain any of the executables that you wish to run on remote systems for
reasons that should become clear later, so it may be best to create a new
folder specifically for sshfsexec and adjust your `PATH` environment variable
accordingly.

    $ mkdir ~/bin/sshfsexec
    $ cp sshfsexec.py ~/bin/sshfsexec
    $ chmod +x ~/bin/sshfsexec/sshfsexec.py
    $ export PATH="$HOME/bin/sshfsexec:$PATH"

If you adjust your `PATH` environment variable, make sure to update your login
profile (generally `~/.profile`) to make sure `PATH` is setup automatically
when you log in. Once sshfsexec has been copied into a folder in `PATH`, create
symlinks from sshfsexec for the commands you wish to execute transparently on
the remote systems. In the example below, `git` will be executed on remote
systems when interacting with SSHFS volumes.

    $ cd ~/bin/sshfsexec
    $ ln -s sshfsexec.py git
    $ hash -r                          # Clears the executable cache in Bash.

Any time `git` is inside of the SSHFS volume, it will not be executed on the
remote system transparently:

    ~$ sshfs codevat.com:/home/git/repositories git.codevat.com/
    ~$ cd git.codevat.com/mydwm.git
    mydwm.git$ git log | head
    commit 8f80c5343a8b430b28d18a6902ed2fbb05a6c90a
    Author: Eric Pruitt
    Date:   Wed Dec 26 19:53:45 2012 -0600

        Folder paths normalized in dwmstatus

        - Folder paths may now end with slashes, and repeated slashes are
          normalized to a single slash.

    commit bfd709906a5a5cc0f3788e342bbc47d7e6aa5a92
    mydwm.git$

That is not a particularly convincing example. Let us compare `git --version`
executed inside the SSHFS volume and the home directory:

    mydwm.git$ git --version
    git version 1.7.1
    Connection to codevat.com closed.
    mydwm.git$ cd
    ~$ git --version
    git version 1.7.2.5

If a command's arguments reference paths that are mounted inside an SSHFS
folder, one's current working directory need not be inside the mount for
transparent usage:

    ~$ git clone ~/git.codevat.com/mydwm.git ~/git.codevat.com/clone.git
    Initialized empty Git repository in /home/git/repositories/clone.git/.git/

By default, only absolute paths, relative paths that start with "./" or
arguments that contain "/../" are translated to remote paths. This means the
following command would be executed locally across SSHFS instead of on the
remote system using native file system access and likely take a long time to
finish:

    ~$ time git clone git.codevat.com/mydwm.git git.codevat.com/clone.git
    Cloning into git.codevat.com/clone.git...
    done.
    real    2m37.094s

This behaviour can be changed with the `translate_all_arguments` configuration
variable or worked-around by using absolute paths or relative paths prefixed
with "./".

Advanced Configuration
----------------------

Configuration is managed with a Python script read from `~/.sshfsexec.conf` or
the path defined by the environment variable `SSHFSEXEC_CONFIG`. This script
will be executed twice, once before the command arguments are translated and
once after. On the first pass, `pre_process_config` is `True` and `False` on
the second pass. During the first execution, `transargs` will not be populated,
and `sshremote` will not yet be set if sshfsexec was run outside of an SSHFS
mount. A sample configuration script can be found in this directory with the in
the file named "config-sample.py". When the script is executed, it is executed
inside of sshfsexec's `main` function with unrestricted access to the code, but
a list of the most relevant variables follows below.

### Options ###

**pre_process_config**: Variable indicating whether or not the configuration
file is being executed before or after the command arguments have been
translated to remote paths.

**command**: Basename of the command being executed / the basename of argv0
when the sshfsexec is launched.

**translate_all_arguments**: Outside of a folder in an SSHFS mount, sshfsexec
will only check to see if an argument is a path mounted in an SSHFS volume if
the argument begins with '/', '../', or './' or contains '/../'. This is to
prevent commands from being executed on remote systems when one of the
command's arguments also happens to share the name of a SSHFS folder. To
demonstrate a use-case, let's pretend we have a server hosting Project X with
the hostname "projectx.company.tld." On my personal computer, I am working on
Project Y which happens to have have a dependency on Project X, so I setup the
following SSHFS mount:

    $ sshfs projectx.company.tld ~/programming/projecty/projectx

Inside of `~/programming/projecty`, I might run `make projectx`. If
`translate_all_arguments` is set, the end result would be `make /home/user/`
being executed on projectx.company.tld via SSH. Without it set, `make projectx`
would be executed locally. The default value for `translate_all_arguments` is
`False`.

**preserve_isatty**: Normally, a TTY will only be allocated on the remote
server if, locally, stdin and stdout are TTY's. This causes problems with
things like `grep --color` and `ls --color=auto`, both of which examine stdout
to determine whether or not colors should be rendered. Setting
`preserve_isatty` will result in some kludgey stuff being done to make sure the
isatty results for stdin, stdout and stderr on the remote process are the same
as for the local process.

In its current implemenation, this option is buggy: anything launched on the
remote server that reads from stdin will hang indefinitely waiting.

### Command Execution Variables ###

**environment**: A dictionary containing the environment variables that will be
passed to the subprocess. Note that for commands run via SSH, the environment
variables are set locally before SSH is launched. This means that environment
variables are subject to the configuration settings on the remote host,
specifically `PermitUserEnvironment`.

**stdin_is_pipe**: Boolean indicating whether or not stdin is a pipe.

**originalargs**: Iterable containing the untranslated arguments passed to the
command.

**sshremote**: Remote SSH system where a command will be executed. This will be
in the form of `user@hostname` or simply `hostname` depending on the arguments
used for `sshfs` and the defined SSH options.

**cwdtranslation**: When a command is launched inside a directory within an
SSHFS mount, `cwdtranslation` is a tuple that contains the remote login
("user@hostname" or "hostname"), the remote directory that corresponds to the
current working directory, and the remote directory that the SSHFS mount point
corresponds to. If a command is launched outside of an SSHFS folder, this
variable will be `None`.

**transargs**: Arguments that will be used to execute the command on the remote
system. If any of the original arguments were paths on the remote system, the
path in `transargs` will be the path as it exists on the remote server instead
of how it was referenced within the SSHFS mount.

To Do
-----

- Allow commands with both local and remote path arguments to be executed on
  both systems. For example `rm here.jpg ~/mounts/sshfs/there.jpg` would run
  `rm here.jpg` locally and `ssh user@remote 'rm there.jpg'`.
- Fix hanging issue with `preserve_isatty`.
