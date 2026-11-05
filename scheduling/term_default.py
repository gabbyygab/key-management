"""The admin's default term, picked once in the navbar.

Every term-aware page asks ``resolve_term`` instead of guessing. The order is:
an explicit ``?term=`` on the page, then the term picked in the navbar (kept in
the session), then the term the guards work from today, then the newest live
term. The navbar pick is per login session and never changes what guards see.
"""
from django.utils import timezone

from .models import Term
from .services import effective_term

SESSION_KEY = "kt_default_term"


def live_terms():
    return Term.objects.filter(is_active=True).order_by("-start_date")


def cached_live_terms():
    """live_terms() as a list, cached until a term is saved (navbar, every page)."""
    from accounts.cache_utils import remember
    return remember("live-terms", ["schedule"], lambda: list(live_terms()))


def default_term_from(request, terms):
    """default_term() over an already-loaded list of live terms (no queries)."""
    raw = request.session.get(SESSION_KEY)
    if raw:
        found = next((t for t in terms if t.pk == raw), None)
        if found:
            return found
    return effective_term(timezone.localdate()) or (terms[0] if terms else None)


def default_term(request, terms=None):
    """The navbar term if it is still live, else the automatic one."""
    terms = live_terms() if terms is None else terms
    raw = request.session.get(SESSION_KEY)
    if raw:
        found = terms.filter(pk=raw).first()
        if found:
            return found
    return effective_term(timezone.localdate()) or terms.first()


def resolve_term(request, param="term"):
    """``(terms, term)``: the page's term picker choices (a list) and the term
    in use. Served from the cached live-term list, so it costs no queries."""
    terms = cached_live_terms()
    raw = (request.GET.get(param, "") or request.POST.get(param, "")).strip()
    if raw.isdigit():
        found = next((t for t in terms if t.pk == int(raw)), None)
        if found:
            return terms, found
    return terms, default_term_from(request, terms)
