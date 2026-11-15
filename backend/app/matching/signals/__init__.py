"""Match signals. Importing this registers them, in this order."""

import app.matching.signals.brand_or_name_in_text  # noqa: F401
import app.matching.signals.code_in_name  # noqa: F401
import app.matching.signals.code_in_text  # noqa: F401
import app.matching.signals.from_product_page  # noqa: F401
import app.matching.signals.looks_like_accepted  # noqa: F401
import app.matching.signals.only_product  # noqa: F401
import app.matching.signals.same_picture  # noqa: F401
