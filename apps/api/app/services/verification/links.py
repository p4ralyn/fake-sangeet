"""Where an ID photo is fetched from.

One function, in its own module, because two unrelated callers need it -- the
review queue and the gate scanner -- and a copy in each is a copy that drifts.
Kept out of `service.py` so `services/access/scans.py` can import it without
pulling the whole verification service in behind it.

**This replaced a signed object-store URL, and the reason is worth keeping.**
The store seam has two backends: real GCS, which signs a public https URL, and
fake-gcs, which cannot sign anything and simply returned its own address. In
production that address was `http://fake-gcs:4443` -- a hostname that exists
only inside the compose network. Handed to a browser as an `<img src>` it could
not work for three separate reasons: the name does not resolve outside Docker,
it is http on an https page, and the port is not exposed. Every ID photo failed
to load, in the review queue and at the gate both.

Serving the bytes through the API instead fixes all three at once, and is the
stronger position anyway: the bucket stays private with no public read path,
and each fetch is authorised against the caller's session rather than being a
bearer URL that works for whoever holds it.
"""
from uuid import UUID

#: Route prefix. The route and this builder must agree, so neither spells the
#: path itself -- see `api/v1/routes/verification.py`.
ID_IMAGE_PREFIX = "/api/v1/id-images"


def id_image_href(id_image_id: UUID) -> str:
    """The same-origin path that serves one ID photo.

    Relative on purpose: the browser resolves it against the page it is already
    on, so it inherits that origin's scheme, host and cookies. An absolute URL
    would need `APP_BASE_URL` to be correct in every environment, which is the
    class of configuration mistake this is fixing.
    """
    return f"{ID_IMAGE_PREFIX}/{id_image_id}"
