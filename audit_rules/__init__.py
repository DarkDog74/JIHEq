from .registry import (
    AuditRuleSpec,
    get_rule_spec,
    list_rule_items,
    register_rule,
)

# 每新增一个稽核规则，在这里导入一次以触发注册。
from . import bean_01 as _bean_01  # noqa: F401,E402
from . import bean_02 as _bean_02  # noqa: F401,E402
from . import dj_01 as _dj_01  # noqa: F401,E402
from . import fx_02 as _fx_02  # noqa: F401,E402
from . import fx_01 as _fx_01  # noqa: F401,E402
from . import fx_03 as _fx_03  # noqa: F401,E402
from . import fx_04 as _fx_04  # noqa: F401,E402
from . import fx_05 as _fx_05  # noqa: F401,E402
from . import fx_06 as _fx_06  # noqa: F401,E402
from . import fx_07 as _fx_07  # noqa: F401,E402
from . import fx_08 as _fx_08  # noqa: F401,E402
from . import fx_09 as _fx_09  # noqa: F401,E402
from . import fx_15 as _fx_15  # noqa: F401,E402
from . import fx_10 as _fx_10  # noqa: F401,E402
from . import fx_11 as _fx_11  # noqa: F401,E402
from . import fx_12 as _fx_12  # noqa: F401,E402
from . import fx_13 as _fx_13  # noqa: F401,E402
from . import fx_14 as _fx_14  # noqa: F401,E402
from . import fx_16 as _fx_16  # noqa: F401,E402
from . import fx_17 as _fx_17  # noqa: F401,E402
from . import fx_19 as _fx_19  # noqa: F401,E402
from . import fx_18 as _fx_18  # noqa: F401,E402
from . import fhx_03 as _fhx_03  # noqa: F401,E402
from . import fhx_02 as _fhx_02  # noqa: F401,E402
from . import fhx_01 as _fhx_01  # noqa: F401,E402
from . import fhx_04 as _fhx_04  # noqa: F401,E402
from . import fhx_05 as _fhx_05  # noqa: F401,E402
from . import dj_02 as _dj_02  # noqa: F401,E402
from . import dj_03 as _dj_03  # noqa: F401,E402
from . import dj_04 as _dj_04  # noqa: F401,E402
from . import dj_05 as _dj_05  # noqa: F401,E402
from . import dj_07 as _dj_07  # noqa: F401,E402
from . import dj_08 as _dj_08  # noqa: F401,E402
from . import dj_09 as _dj_09  # noqa: F401,E402
from . import dj_10 as _dj_10  # noqa: F401,E402
from . import stb_00 as _stb_00  # noqa: F401,E402
from . import stb_04 as _stb_04  # noqa: F401,E402
from . import zm_01 as _zm_01  # noqa: F401,E402
from . import zic_01 as _zic_01  # noqa: F401,E402
from . import zic_02 as _zic_02  # noqa: F401,E402
from . import zic_03 as _zic_03  # noqa: F401,E402
from . import yjya_01 as _yjya_01  # noqa: F401,E402
from . import yjya_02 as _yjya_02  # noqa: F401,E402
from . import yjya_03 as _yjya_03  # noqa: F401,E402
from . import ylbg_01 as _ylbg_01  # noqa: F401,E402
from . import ylbg_02 as _ylbg_02  # noqa: F401,E402
from . import ylbg_03 as _ylbg_03  # noqa: F401,E402
from . import ylbg_04 as _ylbg_04  # noqa: F401,E402
from . import ylbg_05 as _ylbg_05  # noqa: F401,E402
from . import ylbg_06 as _ylbg_06  # noqa: F401,E402
from . import ylp_01 as _ylp_01  # noqa: F401,E402
from . import dcz_01 as _dcz_01  # noqa: F401,E402
from . import dcz_02 as _dcz_02  # noqa: F401,E402
from . import dcz_03 as _dcz_03  # noqa: F401,E402
from . import dcz_04 as _dcz_04  # noqa: F401,E402

__all__ = [
    "AuditRuleSpec",
    "get_rule_spec",
    "list_rule_items",
    "register_rule",
]
