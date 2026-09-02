"""URL routes for the cart app."""
from django.urls import path
from . import views

app_name = "cart"

urlpatterns = [
    # Cart page
    path("", views.cart_detail, name="cart"),

    # POST-only actions
    path("add/", views.cart_add, name="add"),
    path("update/", views.cart_update, name="update"),
    path("remove/", views.cart_remove, name="remove"),

    # PayNow placeholder
    path("pay/", views.pay_now_placeholder, name="pay_now"),
]
