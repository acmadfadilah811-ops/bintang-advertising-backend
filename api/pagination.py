from rest_framework.pagination import PageNumberPagination

class OptionalPageNumberPagination(PageNumberPagination):
    """
    Pagination class yang hanya mengaktifkan pagination jika parameter 'page' atau 'page_size'
    dikirimkan dalam query params. Jika tidak ada parameter tersebut, maka akan mengembalikan
    seluruh list (format array direct) agar kompatibel dengan view/client SPA lama.
    """
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 1000

    def paginate_queryset(self, queryset, request, view=None):
        # Aktifkan pagination hanya jika 'page' atau 'page_size' ada di query params
        if 'page' not in request.query_params and 'page_size' not in request.query_params:
            return None
        return super().paginate_queryset(queryset, request, view)
