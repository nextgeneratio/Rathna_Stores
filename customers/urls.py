"""URL routes for the customers app (customer auth + profile)."""
from django.urls import path
from . import views

app_name = "customers"

urlpatterns = [
    # Authentication (dedicated routes — JS-enhanced into dialog)
    path("login/",    views.customer_login,    name="login"),
    path("register/", views.customer_register, name="register"),
    path("logout/",   views.customer_logout,   name="logout"),

    # Profile
    path("profile/",       views.profile,        name="profile"),
    path("profile/edit/",  views.profile_edit,   name="profile_edit"),

    # Profile image
    path("profile/image/upload/", views.profile_image_upload, name="image_upload"),
    path("profile/image/delete/", views.profile_image_delete, name="image_delete"),

    # Addresses
    path("addresses/",                      views.address_list,    name="address_list"),
    path("addresses/add/",                  views.address_create,  name="address_create"),
    path("addresses/<uuid:address_id>/edit/",   views.address_edit,    name="address_edit"),
    path("addresses/<uuid:address_id>/delete/", views.address_delete,  name="address_delete"),
    path("addresses/<uuid:address_id>/default/",views.address_set_default, name="address_set_default"),

    # Check-your-email landing page (shown when email confirmation required)
    path("confirm-email/", views.confirm_email, name="confirm_email"),
]
