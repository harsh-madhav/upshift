from jinja2 import DictLoader, Environment, Markup, contextfilter, environmentfilter, escape

from .orders import order_total

TEMPLATES = {
    "receipt.html": (
        "<h1>{{ shop_name }} receipt {{ order.id }}</h1>\n"
        "<p>{{ order | customer_label }}</p>\n"
        "<ul>\n"
        "{% for item in order.items %}<li>{{ item.quantity }} x {{ item.sku }}: {{ item.total | money }}</li>\n{% endfor %}"
        "</ul>\n"
        "<p>Total: {{ total | money }}</p>\n"
        "{{ footer }}\n"
    ),
}


@environmentfilter
def money(env, amount):
    return f"{env.globals['currency']}{amount:.2f}"


@contextfilter
def customer_label(context, order):
    return f"{order.customer.name} <{order.customer.email}>, {context['shop_name']} customer"


def make_env(currency: str = "$") -> Environment:
    env = Environment(loader=DictLoader(TEMPLATES), autoescape=True)
    env.globals["currency"] = currency
    env.filters["money"] = money
    env.filters["customer_label"] = customer_label
    return env


def footer(shop_name: str) -> Markup:
    return Markup("<small>Thank you for shopping at ") + escape(shop_name) + Markup("</small>")


def render_receipt(order, shop_name: str = "Upshift Shop", currency: str = "$") -> str:
    template = make_env(currency).get_template("receipt.html")
    return template.render(order=order, shop_name=shop_name, total=order_total(order), footer=footer(shop_name))
