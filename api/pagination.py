from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination


class OptionalPageNumberPagination(PageNumberPagination):
    """Paginate modern callers; cap legacy unpaginated callers safely."""
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 1000
    legacy_max_rows = 1000

    def paginate_queryset(self, queryset, request, view=None):
        if 'page' not in request.query_params and 'page_size' not in request.query_params:
            if queryset.count() > self.legacy_max_rows:
                raise ValidationError({
                    'detail': (
                        f'Data melebihi batas {self.legacy_max_rows} baris. '
                        'Kirim parameter page dan page_size.'
                    )
                })
            return None
        return super().paginate_queryset(queryset, request, view)
