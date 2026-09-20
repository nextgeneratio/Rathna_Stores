-- Supabase Storage RLS policies for Rathna Stores.
-- Run this in the Supabase SQL Editor after creating the User_Profile bucket.
-- Profile objects must use: customers/<customers.customer_id>/profile.<ext>

DROP POLICY IF EXISTS "customer profile images insert" ON storage.objects;
CREATE POLICY "customer profile images insert"
ON storage.objects
FOR INSERT
TO authenticated
WITH CHECK (
  bucket_id = 'User_Profile'
  AND (storage.foldername(name))[1] = 'customers'
  AND EXISTS (
    SELECT 1
    FROM public.customers AS c
    WHERE c.customer_id::text = (storage.foldername(name))[2]
      AND c.auth_user_id = (SELECT auth.uid())
      AND c.is_active = TRUE
  )
);

DROP POLICY IF EXISTS "customer profile images update" ON storage.objects;
CREATE POLICY "customer profile images update"
ON storage.objects
FOR UPDATE
TO authenticated
USING (
  bucket_id = 'User_Profile'
  AND (storage.foldername(name))[1] = 'customers'
  AND EXISTS (
    SELECT 1
    FROM public.customers AS c
    WHERE c.customer_id::text = (storage.foldername(name))[2]
      AND c.auth_user_id = (SELECT auth.uid())
      AND c.is_active = TRUE
  )
)
WITH CHECK (
  bucket_id = 'User_Profile'
  AND (storage.foldername(name))[1] = 'customers'
  AND EXISTS (
    SELECT 1
    FROM public.customers AS c
    WHERE c.customer_id::text = (storage.foldername(name))[2]
      AND c.auth_user_id = (SELECT auth.uid())
      AND c.is_active = TRUE
  )
);

DROP POLICY IF EXISTS "customer profile images delete" ON storage.objects;
CREATE POLICY "customer profile images delete"
ON storage.objects
FOR DELETE
TO authenticated
USING (
  bucket_id = 'User_Profile'
  AND (storage.foldername(name))[1] = 'customers'
  AND EXISTS (
    SELECT 1
    FROM public.customers AS c
    WHERE c.customer_id::text = (storage.foldername(name))[2]
      AND c.auth_user_id = (SELECT auth.uid())
      AND c.is_active = TRUE
  )
);

-- The application stores get_public_url() values in customers.profile_image_url.
-- Set User_Profile to Public in Storage if profile images should be viewable by
-- visitors. Otherwise replace get_public_url() with signed URLs before enabling
-- private access; do not add a public SELECT policy to a private bucket.