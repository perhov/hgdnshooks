#!/usr/bin/env bash
#
# Perform error checking on repository before accepting
# incoming changesets to be added. To be used in the
# repository on the DNS server. Usage:
#
# [hooks]
# prechangegroup = .hg/prechangegroup.sh
#

if hg status | grep .
then
	echo
	echo "ERROR: Local modifications done to server repo."
	echo "($(hostname):$PWD)"
	echo "Please fix, then retry."
	echo
	exit 1
elif [ "$(hg branch)" == "default" ]
then
	echo
	echo "ERROR: Wrong branch checked out on server repo."
	echo "($(hg branch))"
	echo "Please fix, then retry."
	echo
	exit 1
fi
exit 0
