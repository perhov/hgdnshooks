#!/usr/bin/env python
# -*- encoding: utf-8 -*-
#
# changegroup.py
#
# Dependencies:
#   python
#   pyparsing
#   named-checkzone
#   named-checkconf
#


# Configuration options
source_branch="default"
production_branch="production"
serialfile=".hg/serialnumber"

hg_cmd="/usr/bin/hg"
checkconf_cmd="/usr/sbin/named-checkconf"
reload_cmd="/usr/sbin/rndc reload"


import os
import re
import sys
import datetime
import pyparsing
import socket
import subprocess
import tempfile


def print_indented(msg, longmsg=None):
    """Pretty-print single- or multi-line messages to stdout."""
    if msg:
        print " "*10 + msg
    if longmsg:
        for line in longmsg.splitlines():
            print " "*10 + line


def load_serialnumber(serialfile):
    """Allocate and return a new zone file serial number."""
    try:
        savednumber = int(open(serialfile).readline())
    except:
        savednumber = 0
    todaysnumber = int(datetime.datetime.now().strftime("%Y%m%d00"))
    serialnumber = max(savednumber+1, todaysnumber) % 2**32
    return serialnumber


def save_serialnumber(serialfile, serialnumber):
    """Save the last used serial number for next time."""
    f = open(serialfile, "w")
    f.write("%d\n" % serialnumber)
    f.close()


def parse_named_conf(configfile):
    """Parse named.conf and return a 2-tuple of dictionaries.
    
    The first tuple element is a dictionary of the statements inside
    the "options" statement(s) in named.conf.
    
    The second tuple element is a dictionary where the key is the
    DNS name of each zone defined in named conf and the value is a
    dictionary of the zone's configuration options.
    """
    global checkconf_cmd
    try:
        subprocess.check_output([checkconf_cmd, configfile], stderr=subprocess.STDOUT)
    except OSError as e:
        e.filename = checkconf_cmd
        raise
    p = pyparsing
    toplevel = p.Forward()
    keyword = (p.Word(p.alphanums + "-_.*!/:") |
               p.quotedString.setParseAction(p.removeQuotes))
    nestedstatement = p.Group(p.Suppress("{") +
                              p.OneOrMore(toplevel) +
                              p.Suppress("}"))
    options = p.Group(p.Suppress(p.Literal("options")) +
                      nestedstatement +
                      p.Suppress(";")).setResultsName("options")
    zone = p.Group(p.Suppress(p.Literal("zone")) +
                   p.ZeroOrMore(keyword | nestedstatement) +
                   p.Suppress(";")).setResultsName("zones", listAllMatches=True)
    statement = p.Group(keyword +
                        p.ZeroOrMore(keyword | nestedstatement) +
                        p.Suppress(";"))
    toplevel << p.OneOrMore(zone | options | statement)
    # (zone | statement) is equivalent to p.MatchFirst([zone, statement])
    toplevel.ignore(p.cStyleComment)
    toplevel.ignore(p.dblSlashComment)
    toplevel.ignore(p.pythonStyleComment)
    parser = toplevel
    result = parser.parseFile(configfile)
    #pprint(result.asList())
    options={'directory': ''}
    if result.options:
        for key,value in result.options[0]:
            options[key] = value
    zones={}
    for name, opts in result.zones:
        zones[name] = dict(opts.asList())
    return (options, zones)


def whoami():
    """Return a string to be used as author of the commits we do."""
    script = sys.argv[0]
    host = socket.getfqdn()
    return "%s:%s" % (host, script)


def hg(*args):
    """Run mercurial with the given argument(s)."""
    global hg_cmd
    cmd = (hg_cmd,) + args
    return subprocess.check_output(cmd, stderr=subprocess.STDOUT).rstrip("\n")


def list_modified_files():
    """Return a list of the files changed or added since the last commmit."""
    files = hg('status', '-amn')
    return files.splitlines()


def update_zonefile(filename, serialnumber):
    """Increment the serial number in the SOA record of the zone file."""
    search = r"^(\s*)([0-9]+)(\s*;\s*Serialnumber\s*)$"
    replace = r"\g<1>%s\g<3>" % serialnumber
    rx = re.compile(search, re.MULTILINE)
    contents = open(filename).read()
    if not rx.search(contents):
        return False
    contents = rx.sub(replace, contents)
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(contents)
    os.chmod(tmp.name, 0644)
    os.rename(tmp.name, filename)
    return True


def merge():
    """Merge changes from the main branch onto the production branch.
    
    Use mercurial's 'internal:other' algorithm to avoid merge conflicts.
    Changes from the main branch take precedence over changes in the
    production branch. This can happen if the SOA serial number is
    changed on the main branch.
    """
    global source_branch
    hg('merge', '-q', '-t', 'internal:other', '-r', source_branch)
    return hg('status', '-amn')


def get_includes(zonefile):
    """Return a list of the external files included by a zone file.
    
    Files must be included with RFC 1035 syntax.
    Nested includes are not supported.
    """
    pattern = r"""
        ^\s*\$INCLUDE\s+     # $INCLUDE directive (ignore leading space)
        (?P<filename>[^\s]+) # Zonefile
        # Optional domain name
        (
          \s+                # Ignore space between filename and domain
          (?P<domain>[^\s]+) # Domain name
        )?
        \s*$                 # Ignore trailing space
    """
    rx = re.compile(pattern, re.VERBOSE)
    files = set()
    for line in open(zonefile).read().splitlines():
        match = rx.match(line)
        if match:
            files.add(match.group('filename'))
    # The included files are relative to the zone file, which is
    # relative to the options->directory statement in named.conf,
    # which may or may not coincide with the repository root.
    # We want file names relative to the repository root:
    zonefiledir = os.path.dirname(zonefile)
    reporoot = hg('root')
    files = [os.path.join(zonefiledir, f) for f in files]
    files = [os.path.relpath(f, start=reporoot) for f in files]
    return files
    

def generate_dependencies(named_conf):
    """Return a dictionary representing the reverse dependency graph of
    the files included by the zone files defined in named.conf.
    
    The keys of the dictionary are the zone files referenced to by
    named.conf, as well as any files included by these zones.

    The values are a list of the zone files that are affected by a
    change in that file.
    
    A zone file depends implicitly on itself.
    """
    options, zones = parse_named_conf(named_conf)
    reporoot = hg('root')
    reverse_deps = {}
    for zone, opts in zones.iteritems():
        zonefile = os.path.join(options['directory'], opts['file'])
        zonefile = os.path.relpath(zonefile, start=reporoot)
        try:
            includes = get_includes(zonefile)
            for file in [zonefile] + includes:
                reverse_deps.setdefault(file, []).append(zonefile)
        except IOError as e:
            print_indented("%s: %s" % (e.filename, e.strerror))
    files = set()
    for filename in list_modified_files():
        if filename in reverse_deps:
            files.update(reverse_deps[filename])
    return files
 

def auto_increment(zonefiles, serialnumber):
    """Increment the serial number of the zone files and pretty-print
    diagnostic output to stdout. Zone files outside of the repository
    are ignored.
    """
    all_ok = True
    reporoot = os.path.realpath(hg('root')) + '/'
    fmt = "%%-%ds" % max(map(len, zonefiles))
    for filename in zonefiles:
        if not os.path.realpath(filename).startswith(reporoot):
            print_indented(fmt % filename + " => ignored")
            continue
        try:
            if update_zonefile(filename, serialnumber):
                print_indented(fmt % filename + " => %s" % serialnumber)
            else:
                print_indented(fmt % filename + " => failed")
                all_ok = False
        except IOError as e:
            print_indented(fmt % filename + " => %s" % e.strerror)
            all_ok = False
    return all_ok


def commit(serialnumber):
    """Make a summary of the merged changes and commit them."""
    global source_branch, production_branch
    if serialnumber:
        shortmsg = "Autocommit (%s)" % serialnumber
    else:
        shortmsg = "Autocommit (no serial update)"
    # Generate a list of the merged changesets (that is, those committed
    # to the source branch but not (yet) the production branch).
    longmsg = hg('log', '-r', source_branch+':0', '-P', production_branch,
                 '--template', '    {node|short}: {desc|firstline}\n')
    commitmsg = "%s\nMerged changesets:\n%s" % (shortmsg, longmsg)
    hg('commit', '-u', whoami(), '-m', commitmsg)
    info = hg('tip', '--template', 'Revision {node|short} on branch \'{branch}\'')
    return info


def reload():
    """Reload the name server."""
    global reload_cmd
    subprocess.check_output(reload_cmd, shell=True, stderr=subprocess.STDOUT)



def main(named_conf):
    global serialfile, source_branch, production_branch
    if hg('branch') != production_branch:
        print "ERROR: This script should only be run as a hook"
        print "(in the '%s' branch on the DNS server repo)." % production_branch
        return False

    # Step 0/5:
    errors = False

    # Step 1/5: Merge endringer fra 'default'-branchen.
    print "Step 1/5: Merging file(s):"
    try:
        files = merge()
    except subprocess.CalledProcessError as e:
        # Merge failed, bail out
        print_indented("ERROR: Merge failed:", e.output)
        print_indented("Manual intervention required. Sorry!")
        return False
    print_indented(None, files)

    # Step 2/5: Generate dependencies
    print "Step 2/5: Generating dependencies from '%s':" % named_conf
    zonefiles = None
    try:
        zonefiles = generate_dependencies(named_conf)
    except (IOError, OSError) as e:
        print_indented("ERROR: %s: %s" % (e.filename, e.strerror))
        errors = True
    except subprocess.CalledProcessError as e:
        print_indented("ERROR: named-checkconf failed:", e.output)
        errors = True

    # Step 3/5: Autoincrement
    print "Step 3/5: Incrementing serial numbers:"
    serialnumber = load_serialnumber(serialfile)
    if zonefiles is None:
        print_indented("(skipped due to missing dependencies)")
    elif len(zonefiles):
        if not auto_increment(zonefiles, serialnumber):
            errors = True

    # Step 4/5: Commit
    print "Step 4/5: Committing:"
    try:
        info = commit(serialnumber)
    except subprocess.CalledProcessError as e:
        # Commit failed, bail out
        print_indented("ERROR: commit failed:", e.output)
        errors = True
    print_indented(info)

    # Step 4b: Save serialnumber (silently), but only if it's been used
    try:
        if zonefiles and len(zonefiles):
            save_serialnumber(serialfile, serialnumber)
    except IOError as e:
        print_indented("ERROR: Couldn't save '%s': %s" % (e.filename, e.strerror))
        errors = True

    # Step 5/5: Reload
    if errors:
        print "Step 5/5: NOT reloading nameserver due to errors."
        return False
    else:
        print "Step 5/5: Reloading nameserver:"
        try:
            reload()
            print_indented("OK")
        except subprocess.CalledProcessError as e:
            print_indented("ERROR: reload failed:", e.output)
            return False
    print "All steps completed successfully!"
    return True



if __name__ == '__main__':
    print "=" * 64
    if len(sys.argv) != 2:
        print "ERROR: Wrong usage. Usage: changegroup.py path/to/named.conf"
        exit_status = 2
    else:
        named_conf = sys.argv[1]
        rc = main(named_conf)
        exit_status = 0 if rc==True else 1
    print "=" * 64
    sys.exit(exit_status)
