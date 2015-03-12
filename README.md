# hgdnshooks



## About

**hgdnshooks** is a set of
[Mercurial](http://mercurial.selenic.com/)
hooks for DNS zone data.

Intended for [BIND](https://www.isc.org/downloads/bind/)
zone files stored in a Mercurial repository,
these hooks will automate deployment of zone updates.

**hgdnshooks** lets you edit your zone files locally,
and deploy them to your DNS server with `hg push`.
When pushing to the server, the zones are checked
with `named-checkzone`, SOA serial numbers are incremented,
and the zones are reloaded.



## How does it work?

**hgdnshooks** uses two branches (_default_ and _production_).
The _default_ branch is where you commit your changes,
and the _production_ branch is used by the DNS server.
When changes are pushed to the DNS server,
this happens in the server repo:

  1. All new changesets on the _default_ branch are merged into
     the _production_ branch.
  2. The _named.conf_ file is read and processed to build a list of
     which zone files should be checked.
  3. These zone files are scanned for `$INCLUDE` directives
     to build a dependency graph.
  4. This dependency graph is used to increment the SOA serial number
     of the zones that have changed since the previous `hg push`.
     (A zone's serial number needs to be incremented if the merge
     touched the zone file itself, or any of the files the zone file
     includes. Unchanged zone files keep their old SOA serial number.)
  5. A commit is made to the _production_ branch. For simplicity, the
     commit message contains information about which changesets where
     merged, and which serial number the zones were incremented to.


## Installing hgdnshooks

### Step 1: Check requirements

hgdnshooks requires:

  * [python](http://www.python.org) 2.6 or newer
  * [pyparsing](https://pypi.python.org/pypi/pyparsing) 1.5 or newer

The existing zone files needs to have the SOA serial number on a line
by itself, and the line must end with the comment `; Serialnumber`.
Example:

    :::text
    $ORIGIN example.com.
    @	IN	SOA	ns.example.com. hostmaster.example.com. (
    				0000000000 ; Serialnumber
    				3600    ; Refresh
    				900     ; Retry
    				604800  ; Expire
    				3600 )  ; Minimum TTL

### Step 2: Create the repository

Convert the directory containing your DNS zone data to a Mercurial
repository (or, if you have an existing repository, push it to the
DNS server):

    :::text
    server:/# cd /etc/bind
    server:/etc/bind# hg init
    server:/etc/bind# hg add
    server:/etc/bind# hg commit

Then create the _production_ branch and switch to it:

    :::text
    server:/etc/bind# hg branch production

### Step 3: Install hgdnshooks

Clone the hgdnshooks repository into a subdirectory of the `.hg`
directory on the DNS server:

    :::text
    server:/etc/bind# cd .hg
    server:/etc/bind/.hg# hg clone https://bitbucket.org/perhov/hgdnshooks

A directory `/etc/bind/.hg/hgdnshooks` will appear, containing the
`prechangegroup.sh` and `changegroup.py` scripts.

### Step 4: Add the hooks

Add the following hooks to the `.hg/hgrc` file:

    :::text
    [hooks]
    prechangegroup = sh .hg/hgdnshooks/prechangegroup.sh
    changegroup = python .hg/hgdnshooks/changegroup.py named.conf

This assumes that your BIND configfile is `named.conf` and is located
in the root (top-level directory) of your repository. If not, replace
`named.conf` with the name of your config file (either an absolute
path, or a path relative to the repository root).



## Using hgdnshooks

With **hgdnshooks**, you should never edit the zone files on the DNS
server. All edits should be done on the _default_ branch on a
repository clone, and then pushed back to the server to go live.

If you want, you can replace the SOA serial number in your zone files
(on the _default_ branch) with 00000000 to remind you that they don't
need to be updated manually.

If you always run `hg push` interactively (rather than from cron or
some automated system), you may want to set `ansi_colors=True` in
`changegroup.py` to get errors printed in red and success in green.

The serial number format used is YYYYMMDDNN, but **hgdnshooks** handles
more than 100 updates/day fine (it will start using tomorrow's serials
if necessary.) The serial number wraps around at 2³²-1, and rollover is
handled according to [RFC 1982](http://tools.ietf.org/rfc/rfc1982.txt).



## Examples

### Example 1: Zone without $INCLUDE

In this example, the zone files are stored in the `pz/` subdirectory:

    :::text
    client:/path/to/clone$ emacs pz/example.com
      <modify zone data>
    client:/path/to/clone$ hg commit
    client:/path/to/clone$ hg push
    pushing to ssh://server//etc/bind
    searching for changes
    adding changesets
    adding manifests
    adding file changes
    added 1 changesets with 1 changes to 1 files (+1 heads)
    ================================================================
    Step 1/5: Merging file(s):
              pz/example.com
    Step 2/5: Generating dependencies from 'named.conf':
    Step 3/5: Incrementing serial numbers:
              pz/example.com => 2014011100
    Step 4/5: Committing:
              Revision 4c6a148a37d2 on branch 'production'
    Step 5/5: Reloading nameserver:
              OK
    ================================================================
    All steps completed successfully!
    ================================================================

Even though you may have several zones configured, only the changed
zone is incremented.

### Example 2: Zone with $INCLUDE

In this example, the zone files are stored in the `pz/` subdirectory,
and they include some files from the `inc/` subdirectory.
The `pz/example.com` zone file contains this line:

    :::text
    $INCLUDE inc/subdomain.example.com

and when you edit this file, hgdnshooks increments the SOA serial
number of example.com:

    :::text
    client:/path/to/clone$ emacs inc/subdomain.example.com
    client:/path/to/clone$ hg commit
    client:/path/to/clone$ hg push
    pushing to ssh://server//etc/bind
    searching for changes
    adding changesets
    adding manifests
    adding file changes
    added 1 changesets with 1 changes to 1 files (+1 heads)
    ================================================================
    Step 1/5: Merging file(s):
              inc/subdomain.example.com
    Step 2/5: Generating dependencies from 'named.conf':
    Step 3/5: Incrementing serial numbers:
              pz/example.com => 2014011102
    Step 4/5: Committing:
              Revision b3d95d3a8abc on branch 'production'
    Step 5/5: Reloading nameserver:
              OK
    ================================================================
    All steps completed successfully!
    ================================================================

If you have several zones including the same file (e.g. _example.com_
and _example.net_ including common data from a single file), both
zone files will be incremented if you change the included file.



## Known bugs

Automatic serial number rollover will stop working in
[year 2147](https://bitbucket.org/perhov/hgdnshooks/commits/5fafce12c7055509983638221fa9e51f9785dda9).
