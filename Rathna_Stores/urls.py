"""
URL configuration for Rathna_Stores project.
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    # Public cake catalogue — root of the site
    path("", include("cakes.urls", namespace="cakes")),

    # Guest / authenticated cart
    path("cart/", include("cart.urls", namespace="cart")),

    # Customer account (register, login, logout, profile, addresses)
    path("account/", include("customers.urls", namespace="customers")),

    # Staff product management interface
    path("manage/", include("admin.urls", namespace="store_admin")),

    # Django built-in admin (auth/user management for staff)
    path("django-admin/", admin.site.urls),

    # Django auth — staff login/logout only
    path("accounts/", include("django.contrib.auth.urls")),
]
