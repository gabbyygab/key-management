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
