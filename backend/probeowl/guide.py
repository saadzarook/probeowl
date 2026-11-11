# Author: @SaadZarook
"""Where the ProbeOwl guide is published: the one place its address is written.

The CLI prints it, since owl serve may not be running then, and the guide's own site
configuration reads it (guide/hooks.py sets mkdocs.yml's site_url from it), so moving the guide
changes this line only. The landing page (site/index.html) sits at the site's root, and the
guide under /guide/. The web app links to the copy owl serve serves at /guide/ instead.
"""

GUIDE_URL = "https://saadzarook.github.io/probeowl/guide/"


def guide_page(path: str) -> str:
    """The address of one page of the guide, such as guide_page("first-application/")."""
    return GUIDE_URL + path
