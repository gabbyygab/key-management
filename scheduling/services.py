from facilities.models import Room


def find_free_rooms(*, day_of_week, start_time, end_time, term=None,
                    building=None, room_type=None, min_capacity=None):
    """Rooms available for a weekly slot.

    STUB (F5.3): there is no ScheduleBlock model yet (B5.2), so this returns
    every bookable room matching the filters and ignores day/time/term.
    B5.4 replaces the body with the real query - keep the signature and the
    return type (a Room queryset with building selected) so the room finder
    keeps working unchanged.
    """
    qs = (Room.objects.select_related("building")
          .filter(is_active=True, building__is_active=True)
          .exclude(status__in=[Room.Status.MAINTENANCE, Room.Status.INACTIVE]))
    if building is not None:
        qs = qs.filter(building=building)
    if room_type:
        qs = qs.filter(room_type=room_type)
    if min_capacity:
        qs = qs.filter(capacity__gte=min_capacity)
    return qs.order_by("building__code", "room_code")
