# -*- coding: utf-8 -*-
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase


class SharedComponentsTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()

    # -------------------------------------------------------------------------
    # Status Pill Tests
    # -------------------------------------------------------------------------

    def test_status_pill_key_statuses(self):
        """Status pill correctly renders all Key model statuses."""
        # AVAILABLE -> Green
        html = render_to_string("partials/status_pill.html", {"status": "AVAILABLE"})
        self.assertIn("bg-green-50", html)
        self.assertIn("text-green-800", html)
        self.assertIn("AVAILABLE", html)

        # BORROWED -> Blue
        html = render_to_string("partials/status_pill.html", {"status": "BORROWED"})
        self.assertIn("bg-blue-50", html)
        self.assertIn("text-blue-800", html)
        self.assertIn("BORROWED", html)

        # OVERDUE -> Red
        html = render_to_string("partials/status_pill.html", {"status": "OVERDUE"})
        self.assertIn("bg-red-50", html)
        self.assertIn("text-red-800", html)
        self.assertIn("OVERDUE", html)

        # LOST -> Red
        html = render_to_string("partials/status_pill.html", {"status": "LOST"})
        self.assertIn("bg-red-50", html)
        self.assertIn("text-red-800", html)
        self.assertIn("LOST", html)

        # DAMAGED -> Amber
        html = render_to_string("partials/status_pill.html", {"status": "DAMAGED"})
        self.assertIn("bg-amber-50", html)
        self.assertIn("text-amber-800", html)
        self.assertIn("DAMAGED", html)

        # INACTIVE -> Stone
        html = render_to_string("partials/status_pill.html", {"status": "INACTIVE"})
        self.assertIn("bg-stone-100", html)
        self.assertIn("text-stone-700", html)
        self.assertIn("INACTIVE", html)

    def test_status_pill_transaction_statuses(self):
        """Status pill correctly renders all BorrowTransaction statuses."""
        # ACTIVE -> Blue
        html = render_to_string("partials/status_pill.html", {"status": "ACTIVE"})
        self.assertIn("bg-blue-50", html)
        self.assertIn("text-blue-800", html)
        self.assertIn("ACTIVE", html)

        # RETURNED -> Green
        html = render_to_string("partials/status_pill.html", {"status": "RETURNED"})
        self.assertIn("bg-green-50", html)
        self.assertIn("text-green-800", html)
        self.assertIn("RETURNED", html)

    def test_status_pill_room_statuses(self):
        """Status pill correctly renders all Room statuses."""
        # AVAILABLE -> Green
        html = render_to_string("partials/status_pill.html", {"status": "AVAILABLE"})
        self.assertIn("bg-green-50", html)

        # OCCUPIED -> Blue
        html = render_to_string("partials/status_pill.html", {"status": "OCCUPIED"})
        self.assertIn("bg-blue-50", html)
        self.assertIn("text-blue-800", html)
        self.assertIn("OCCUPIED", html)

        # MAINTENANCE -> Amber
        html = render_to_string("partials/status_pill.html", {"status": "MAINTENANCE"})
        self.assertIn("bg-amber-50", html)
        self.assertIn("text-amber-800", html)
        self.assertIn("MAINTENANCE", html)

    def test_status_pill_approval_statuses(self):
        """Status pill correctly renders User approval statuses."""
        # PENDING -> Amber
        html = render_to_string("partials/status_pill.html", {"status": "PENDING"})
        self.assertIn("bg-amber-50", html)
        self.assertIn("text-amber-800", html)
        self.assertIn("PENDING", html)

        # APPROVED -> Green
        html = render_to_string("partials/status_pill.html", {"status": "APPROVED"})
        self.assertIn("bg-green-50", html)
        self.assertIn("text-green-800", html)
        self.assertIn("APPROVED", html)

        # REJECTED -> Red
        html = render_to_string("partials/status_pill.html", {"status": "REJECTED"})
        self.assertIn("bg-red-50", html)
        self.assertIn("text-red-800", html)
        self.assertIn("REJECTED", html)

    def test_status_pill_user_roles(self):
        """Status pill correctly renders all User roles with distinct styling."""
        # ADMIN -> Purple
        html = render_to_string("partials/status_pill.html", {"status": "ADMIN"})
        self.assertIn("bg-purple-50", html)
        self.assertIn("text-purple-800", html)
        self.assertIn("ADMIN", html)

        # FACILITIES -> Teal
        html = render_to_string("partials/status_pill.html", {"status": "FACILITIES"})
        self.assertIn("bg-teal-50", html)
        self.assertIn("text-teal-800", html)
        self.assertIn("FACILITIES", html)

        # CUSTODIAN -> Indigo
        html = render_to_string("partials/status_pill.html", {"status": "CUSTODIAN"})
        self.assertIn("bg-indigo-50", html)
        self.assertIn("text-indigo-800", html)
        self.assertIn("CUSTODIAN", html)

        # GUARD -> Emerald
        html = render_to_string("partials/status_pill.html", {"status": "GUARD"})
        self.assertIn("bg-emerald-50", html)
        self.assertIn("text-emerald-800", html)
        self.assertIn("GUARD", html)

        # FACULTY_STAFF -> Stone
        html = render_to_string("partials/status_pill.html", {"status": "FACULTY_STAFF"})
        self.assertIn("bg-stone-100", html)
        self.assertIn("text-stone-700", html)
        self.assertIn("FACULTY / STAFF", html)

    def test_status_pill_custom_label_and_case_insensitivity(self):
        """Status pill supports custom labels and handles lowercase input."""
        html = render_to_string(
            "partials/status_pill.html",
            {"status": "admin", "label": "Super Administrator"},
        )
        self.assertIn("bg-purple-50", html)
        self.assertIn("Super Administrator", html)

        html = render_to_string(
            "partials/status_pill.html",
            {"status": "pending", "label": "Under Review"},
        )
        self.assertIn("bg-amber-50", html)
        self.assertIn("Under Review", html)

    def test_status_pill_dot_and_fallback(self):
        """Status pill has aria-hidden dot and falls back for unknown statuses."""
        html = render_to_string("partials/status_pill.html", {"status": "CUSTOM_STATUS"})
        self.assertIn('aria-hidden="true"', html)
        self.assertIn("bg-stone-100", html)
        self.assertIn("CUSTOM_STATUS", html)

    # -------------------------------------------------------------------------
    # Stat Card Tests
    # -------------------------------------------------------------------------

    def test_stat_card_basic(self):
        """Stat card renders label, value, sub, and default icon."""
        html = render_to_string(
            "partials/stat_card.html",
            {"label": "Total Keys", "value": 150, "sub": "4 buildings"},
        )
        self.assertIn("Total Keys", html)
        self.assertIn("150", html)
        self.assertIn("4 buildings", html)
        self.assertIn('data-lucide="key-round"', html)
        self.assertIn("<div", html)
        self.assertNotIn("<a", html)

    def test_stat_card_custom_icon(self):
        """Stat card respects custom icon, icon_bg, and icon_color."""
        html = render_to_string(
            "partials/stat_card.html",
            {
                "label": "Overdue",
                "value": 5,
                "icon": "alert-triangle",
                "icon_bg": "bg-red-50",
                "icon_color": "text-red-700",
            },
        )
        self.assertIn('data-lucide="alert-triangle"', html)
        self.assertIn("bg-red-50", html)
        self.assertIn("text-red-700", html)

    def test_stat_card_click_through_link(self):
        """Stat card renders as link when url/href is provided."""
        html = render_to_string(
            "partials/stat_card.html",
            {
                "label": "Available Keys",
                "value": 42,
                "url": "/keys/?status=AVAILABLE",
            },
        )
        self.assertIn('<a href="/keys/?status=AVAILABLE"', html)
        self.assertIn('data-lucide="arrow-up-right"', html)
        self.assertIn("group", html)

    def test_stat_card_trend_indicator(self):
        """Stat card renders positive, negative, and sentiment-overridden trends."""
        # Upward positive trend
        html = render_to_string(
            "partials/stat_card.html",
            {
                "label": "Returns",
                "value": 85,
                "trend": "+12%",
                "trend_direction": "up",
                "trend_label": "vs yesterday",
            },
        )
        self.assertIn("+12%", html)
        self.assertIn("vs yesterday", html)
        self.assertIn("bg-green-50", html)
        self.assertIn('data-lucide="trending-up"', html)

        # Downward negative trend
        html = render_to_string(
            "partials/stat_card.html",
            {
                "label": "Inventory",
                "value": 90,
                "trend": "-5%",
                "trend_direction": "down",
            },
        )
        self.assertIn("-5%", html)
        self.assertIn("bg-red-50", html)
        self.assertIn('data-lucide="trending-down"', html)

        # Upward trend with danger sentiment (e.g. overdue keys increasing)
        html = render_to_string(
            "partials/stat_card.html",
            {
                "label": "Overdue",
                "value": 14,
                "trend": "+4",
                "trend_direction": "up",
                "trend_sentiment": "danger",
            },
        )
        self.assertIn("+4", html)
        self.assertIn("bg-red-50", html)

    # -------------------------------------------------------------------------
    # Empty State Tests
    # -------------------------------------------------------------------------

    def test_empty_state_standalone(self):
        """Empty state renders as standalone block with defaults and custom action."""
        html = render_to_string(
            "partials/empty_state.html",
            {
                "title": "No keys found",
                "message": "Try adjusting your search criteria.",
                "action_url": "/keys/create/",
                "action_label": "+ Add key",
                "action_icon": "plus",
                "secondary_url": "/keys/",
                "secondary_label": "Clear search",
            },
        )
        self.assertIn("<div", html)
        self.assertIn("No keys found", html)
        self.assertIn("Try adjusting your search criteria.", html)
        self.assertIn('href="/keys/create/"', html)
        self.assertIn("+ Add key", html)
        self.assertIn('href="/keys/"', html)
        self.assertIn("Clear search", html)
        self.assertIn('data-lucide="inbox"', html)

    def test_empty_state_table_row(self):
        """Empty state renders as <tr> with colspan when colspan is provided."""
        html = render_to_string(
            "partials/empty_state.html",
            {
                "colspan": 6,
                "title": "No transactions recorded",
                "action_modal": "borrow-modal",
                "action_label": "Release Key",
            },
        )
        self.assertIn('<tr class="empty-state-row">', html)
        self.assertIn('<td colspan="6"', html)
        self.assertIn('data-modal-open="borrow-modal"', html)
        self.assertIn("Release Key", html)

    # -------------------------------------------------------------------------
    # Loading Skeleton Tests
    # -------------------------------------------------------------------------

    def test_loading_skeleton_list_and_card(self):
        """Loading skeleton renders animated pulse placeholders for lists and cards."""
        # List skeleton
        html = render_to_string("partials/loading_skeleton.html", {"type": "list"})
        self.assertIn("animate-pulse", html)
        self.assertIn('role="status"', html)
        self.assertIn('aria-busy="true"', html)
        self.assertIn("sr-only", html)

        # Card skeleton
        html = render_to_string("partials/loading_skeleton.html", {"type": "card"})
        self.assertIn("grid grid-cols-2", html)
        self.assertIn("animate-pulse", html)

    def test_loading_skeleton_table_and_rows(self):
        """Loading skeleton renders full table and table rows."""
        # Table rows skeleton
        html = render_to_string("partials/loading_skeleton.html", {"type": "table_rows", "cols": 5})
        self.assertIn("<tr", html)
        self.assertIn("<td", html)
        self.assertIn("animate-pulse", html)

        # Full table skeleton
        html = render_to_string("partials/loading_skeleton.html", {"type": "table"})
        self.assertIn("<table", html)
        self.assertIn("<thead", html)
        self.assertIn("<tbody", html)

    # -------------------------------------------------------------------------
    # Pagination Tests
    # -------------------------------------------------------------------------

    def test_pagination_with_page_obj(self):
        """Pagination renders 'Showing X-Y of Z' and preserves query parameters."""
        items = list(range(1, 101))
        paginator = Paginator(items, 25)
        page_obj = paginator.get_page(2)

        request = self.rf.get("/keys/?q=master&status=AVAILABLE&page=2")
        context = {
            "page_obj": page_obj,
            "request": request,
            "item_label": "keys",
        }
        html = render_to_string("partials/pagination.html", context)

        # Showing X-Y of Z
        self.assertIn("Showing", html)
        self.assertIn("26", html)
        self.assertIn("50", html)
        self.assertIn("100", html)
        self.assertIn("keys", html)

        # Navigation links
        self.assertIn('aria-label="Pagination"', html)
        self.assertIn('aria-current="page"', html)
        self.assertIn("Previous", html)
        self.assertIn("Next", html)

        # Query param preservation
        self.assertIn("q=master", html)
        self.assertIn("status=AVAILABLE", html)

    def test_pagination_fallback_total_count(self):
        """Pagination displays total_count when page_obj is not present."""
        html = render_to_string(
            "partials/pagination.html",
            {"total_count": 48, "item_label": "rooms"},
        )
        self.assertIn("Showing", html)
        self.assertIn("48", html)
        self.assertIn("rooms", html)

    # -------------------------------------------------------------------------
    # Table Toolbar Tests
    # -------------------------------------------------------------------------

    def test_table_toolbar_search_filters_actions(self):
        """Table toolbar renders search box, filter dropdowns, and action buttons."""
        context = {
            "q": "Room 101",
            "search_placeholder": "Search keys...",
            "filter_name": "status",
            "filter_label": "Status",
            "filter_options": [("AVAILABLE", "Available"), ("BORROWED", "Borrowed")],
            "filter_selected": "AVAILABLE",
            "filter2_name": "building",
            "filter2_label": "Building",
            "filter2_options": [("MAIN", "Main Hall"), ("SCI", "Science")],
            "filter2_selected": "MAIN",
            "primary_action_url": "/keys/create/",
            "primary_action_label": "+ Add key",
            "secondary_action_url": "/keys/categories/",
            "secondary_action_label": "Categories",
        }
        html = render_to_string("partials/table_toolbar.html", context)

        # Search
        self.assertIn('value="Room 101"', html)
        self.assertIn('placeholder="Search keys..."', html)
        self.assertIn('role="search"', html)

        # Filter 1
        self.assertIn('name="status"', html)
        self.assertIn('value="AVAILABLE" selected', html)

        # Filter 2
        self.assertIn('name="building"', html)
        self.assertIn('value="MAIN" selected', html)

        # Submit and Clear
        self.assertIn("Filter", html)
        self.assertIn("Clear", html)

        # Actions
        self.assertIn('href="/keys/create/"', html)
        self.assertIn("+ Add key", html)
        self.assertIn('href="/keys/categories/"', html)
        self.assertIn("Categories", html)

    def test_table_toolbar_modal_trigger(self):
        """Table toolbar supports modal trigger for primary action."""
        context = {
            "primary_action_modal": "quick-add-modal",
            "primary_action_label": "Quick Add",
            "primary_action_icon": "plus",
        }
        html = render_to_string("partials/table_toolbar.html", context)
        self.assertIn('data-modal-open="quick-add-modal"', html)
        self.assertIn("Quick Add", html)
        self.assertIn('data-lucide="plus"', html)
