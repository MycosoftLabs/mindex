-- Supabase (public): replace auth/profile triggers to use admin_registry instead of hardcoded emails.
-- Depends on: public.admin_registry (see CMMC compliance migrations).
-- Verify trigger names in production match, or attach:
--   ON auth.users: AFTER INSERT -> handle_new_user()
--   ON public.profiles: BEFORE INSERT OR UPDATE -> handle_super_admin_role()

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $function$
DECLARE
  v_role text;
  v_tier text;
BEGIN
  SELECT r.role INTO v_role
  FROM public.admin_registry AS r
  WHERE lower(r.email) = lower(NEW.email)
    AND COALESCE(r.is_active, true) IS true
  ORDER BY CASE r.role
    WHEN 'super_admin' THEN 0
    WHEN 'admin' THEN 1
    WHEN 'staff' THEN 2
    ELSE 3
  END
  LIMIT 1;

  IF v_role = 'super_admin' THEN
    v_tier := 'enterprise';
  ELSIF v_role IS NOT NULL THEN
    v_tier := CASE
      WHEN v_role IN ('admin', 'staff') THEN 'pro'
      ELSE 'free'
    END;
  ELSE
    v_role := 'user';
    v_tier := 'free';
  END IF;

  INSERT INTO public.profiles (
    id,
    email,
    username,
    full_name,
    avatar_url,
    role,
    subscription_tier
  )
  VALUES (
    NEW.id,
    NEW.email,
    split_part(NEW.email, '@', 1),
    COALESCE(
      NULLIF(trim(NEW.raw_user_meta_data->>'full_name'), ''),
      NULLIF(trim(NEW.raw_user_meta_data->>'name'), ''),
      split_part(NEW.email, '@', 1)
    ),
    NULLIF(trim(NEW.raw_user_meta_data->>'avatar_url'), ''),
    v_role,
    v_tier
  );

  RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.handle_super_admin_role()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $function$
DECLARE
  v_role text;
BEGIN
  SELECT r.role INTO v_role
  FROM public.admin_registry AS r
  WHERE lower(r.email) = lower(NEW.email)
    AND COALESCE(r.is_active, true) IS true
  ORDER BY CASE r.role
    WHEN 'super_admin' THEN 0
    WHEN 'admin' THEN 1
    WHEN 'staff' THEN 2
    ELSE 3
  END
  LIMIT 1;

  IF v_role = 'super_admin' THEN
    NEW.role := 'super_admin';
    NEW.subscription_tier := 'enterprise';
  ELSIF v_role IS NOT NULL THEN
    NEW.role := v_role;
    IF v_role IN ('admin', 'staff') AND (
      NEW.subscription_tier IS NULL OR NEW.subscription_tier = 'free'
    ) THEN
      NEW.subscription_tier := 'pro';
    END IF;
  END IF;

  RETURN NEW;
END;
$function$;

COMMENT ON FUNCTION public.handle_new_user() IS
  'On auth.users insert: create profile with role/tier from admin_registry (no hardcoded super-admin email).';

COMMENT ON FUNCTION public.handle_super_admin_role() IS
  'Before profiles insert/update: enforce role/tier for emails listed in admin_registry.';
