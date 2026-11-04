"""Small template helpers shared across KEYTRACK templates."""
from django import template
from django.http import QueryDict

register = template.Library()


@register.simple_tag(takes_context=True)
def page_url(context, number):
    """``?<current filters>&page=N`` for pagination links.

    Uses an explicit ``querystring`` (already without ``page``) when the view
    passed one, otherwise the current request's GET, so filters survive paging
    on every list without each view having to remember to pass it.
    """
    qs = context.get("querystring")
    if qs is not None:
        params = QueryDict(qs, mutable=True)
    else:
        request = context.get("request")
        params = request.GET.copy() if request is not None else QueryDict(mutable=True)
    params["page"] = number
    return "?" + params.urlencode()


@register.filter
def make_list_sizes(_value):
    """The page sizes offered by the Rows per page select."""
    from accounts.crud import PAGE_SIZE_CHOICES
    return PAGE_SIZE_CHOICES


@register.simple_tag(takes_context=True)
def per_page_url(context, size):
    """``?<current filters>&per_page=N`` (back on page 1) for the page-size select."""
    qs = context.get("querystring")
    if qs is not None:
        params = QueryDict(qs, mutable=True)
    else:
        request = context.get("request")
        params = request.GET.copy() if request is not None else QueryDict(mutable=True)
    params.pop("page", None)
    params["per_page"] = size
    return "?" + params.urlencode()


@register.filter
def nav_key(url_name):
    """The sidebar key for a view name.

    An Archived page (``key_archived``) or a create page (``offering_create``)
    belongs to the same nav item as its live list (``key_list``, ``offering_list``),
    so it maps onto it and the sidebar entry stays lit instead of nothing being
    highlighted.
    """
    if url_name:
        if url_name.endswith("_archived"):
            return url_name[: -len("_archived")] + "_list"
        if url_name.endswith("_create"):
            return url_name[: -len("_create")] + "_list"
    return url_name
