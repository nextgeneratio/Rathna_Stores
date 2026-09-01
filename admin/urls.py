"""URL routes for the staff product management interface."""
from django.urls import path
from . import views

app_name = "store_admin"

urlpatterns = [
    # Dashboard
    path("", views.dashboard, name="dashboard"),

    # Product management
    path("products/", views.product_list, name="product_list"),
    path("products/add/", views.product_create, name="product_create"),
    path("products/<uuid:product_id>/edit/", views.product_edit, name="product_edit"),
    path("products/<uuid:product_id>/toggle/", views.product_toggle_active, name="product_toggle"),

    # Image management (within a product)
    path("products/<uuid:product_id>/images/", views.product_images, name="product_images"),
    path("products/<uuid:product_id>/images/upload/", views.image_upload, name="image_upload"),
    path("products/<uuid:product_id>/images/select/", views.image_select_existing, name="image_select"),
    path("images/<uuid:image_id>/delete/", views.image_delete, name="image_delete"),
    path("images/<uuid:image_id>/set-primary/", views.image_set_primary, name="image_set_primary"),
    path("images/<uuid:image_id>/edit/", views.image_edit, name="image_edit"),

    # Category management
    path("categories/", views.category_list, name="category_list"),
    path("categories/add/", views.category_create, name="category_create"),
    path("categories/<uuid:category_id>/edit/", views.category_edit, name="category_edit"),
    path("categories/<uuid:category_id>/toggle/", views.category_toggle_active, name="category_toggle"),
]
