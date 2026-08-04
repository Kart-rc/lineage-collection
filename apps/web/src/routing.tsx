import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type AnchorHTMLAttributes,
  type MouseEvent,
  type PropsWithChildren,
  type ReactNode,
} from "react";


type RouterContextValue = {
  pathname: string;
  navigate: (to: string, replace?: boolean) => void;
};

type RouterProps = PropsWithChildren<{
  initialEntries?: string[];
}>;

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "className" | "href"> & {
  children: ReactNode;
  className?: string;
  replace?: boolean;
  to: string;
};

type NavLinkProps = Omit<LinkProps, "className"> & {
  className?: string | ((state: { isActive: boolean }) => string | undefined);
  end?: boolean;
};


const RouterContext = createContext<RouterContextValue | null>(null);


function useRouter() {
  const router = useContext(RouterContext);
  if (!router) throw new Error("Routing components must be rendered inside a router");
  return router;
}


export function BrowserRouter({ children }: RouterProps) {
  const [pathname, setPathname] = useState(() => window.location.pathname);

  useEffect(() => {
    const handlePopState = () => setPathname(window.location.pathname);
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  const navigate = useCallback((to: string, replace = false) => {
    const method = replace ? "replaceState" : "pushState";
    window.history[method]({}, "", to);
    setPathname(window.location.pathname);
  }, []);

  const value = useMemo(() => ({ pathname, navigate }), [navigate, pathname]);
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}


export function MemoryRouter({ children, initialEntries = ["/"] }: RouterProps) {
  const [pathname, setPathname] = useState(initialEntries[0] ?? "/");
  const navigate = useCallback((to: string) => setPathname(to), []);
  const value = useMemo(() => ({ pathname, navigate }), [navigate, pathname]);
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}


export function Link({ children, onClick, replace, to, ...props }: LinkProps) {
  const { navigate } = useRouter();

  function handleClick(event: MouseEvent<HTMLAnchorElement>) {
    onClick?.(event);
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey ||
      props.target === "_blank"
    ) {
      return;
    }
    event.preventDefault();
    navigate(to, replace);
  }

  return (
    <a href={to} onClick={handleClick} {...props}>
      {children}
    </a>
  );
}


export function NavLink({ className, end = false, to, ...props }: NavLinkProps) {
  const { pathname } = useRouter();
  const isActive = end
    ? pathname === to
    : to === "/"
      ? pathname === "/"
      : pathname === to || pathname.startsWith(`${to}/`);
  const resolvedClassName = typeof className === "function" ? className({ isActive }) : className;
  return (
    <Link
      aria-current={isActive ? "page" : undefined}
      className={resolvedClassName}
      to={to}
      {...props}
    />
  );
}


export function Navigate({ replace = false, to }: { replace?: boolean; to: string }) {
  const { navigate } = useRouter();
  useEffect(() => navigate(to, replace), [navigate, replace, to]);
  return null;
}


export function usePathname() {
  return useRouter().pathname;
}


export function useParams() {
  const pathname = usePathname();
  const segments = pathname.split("/").filter(Boolean).map(decodeURIComponent);
  if (segments[0] === "review" && segments[1]) return { proposalId: segments[1] };
  if (segments[0] === "runs" && segments[1]) return { runId: segments[1] };
  return {};
}
