"""URL routes for the public cakes catalogue."""
from django.urls import path
from . import views

app_name = "cakes"

urlpatterns = [
    # Public catalogue — root page
    path("", views.catalogue, name="catalogue"),
    # Product detail — UUID-based route
    path("product/<uuid:product_id>/", views.product_detail, name="product_detail"),
]
