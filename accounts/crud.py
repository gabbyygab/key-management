"""Shared plumbing for the admin create/edit screens (F4.x).

Every master-data form answers two audiences from one view:

  * a plain GET/POST renders the full-page template (the no-JS fallback);
  * ``GET ?modal=1`` returns just the form partial for modal-form.js, and a
    modal POST answers 201/200 on success or 422 + the re-rendered partial
    (bound data + inline errors) on failure, so the modal stays open with the
    admin's typed input intact.

Business rules still live in each app's forms/services; this only translates
HTTP and writes the audit row.
"""
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render

from audit.services import log_form_save


def is_modal(request):
    return (request.GET.get("modal") == "1"
            or request.headers.get("x-requested-with") == "XMLHttpRequest")


def form_view(request, *, form, full_template, modal_template, context=None,
              redirect_to, success_message, created, save=None):
    """Run a ModelForm through the modal/full-page contract.

    ``save(form)`` may be passed to customise how the object is persisted (set
    created_by, pre-approve a user...). It must return the saved instance.
    ``success_message`` is a string or a callable taking the instance.
    """
    modal = is_modal(request)
    ctx = {"form": form, "is_modal": modal, **(context or {})}

    if request.method == "POST":
        if form.is_valid():
            obj = save(form) if save else form.save()
            log_form_save(request, form, obj, created=created)
            msg = success_message(obj) if callable(success_message) else success_message
            if modal:
                return HttpResponse(msg, status=201 if created else 200)
            messages.success(request, msg)
            return redirect(redirect_to)
        if modal:
            return render(request, modal_template, ctx, status=422)

    if modal:
        return render(request, modal_template, ctx)
    return render(request, full_template, ctx)


def querystring_without_page(request):
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()
