"""Code that runs INSIDE the Windows guests (pushed to C:\\ib\\agent\\).

Host-side this is a normal package (tests import guest_agent.offsets etc.);
in-guest the files are pushed flat into one directory and import each other as
siblings. Keep intra-package imports written to work BOTH ways (see offsets.py
docstring for the pattern).
"""
