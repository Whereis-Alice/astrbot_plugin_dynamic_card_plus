"""Load the plugin as a package, as AstrBot does, without starting a bot."""

import sys
from pathlib import Path
from types import ModuleType

package = ModuleType("dynamic_card_plugin")
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[package.__name__] = package
