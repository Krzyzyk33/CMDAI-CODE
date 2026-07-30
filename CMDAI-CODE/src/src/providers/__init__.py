import os
import importlib
import pkgutil

                                
                                             
PROVIDERS = {}

def load_providers():
    global PROVIDERS
    PROVIDERS.clear()
    
    package_dir = os.path.dirname(__file__)
    for _, module_name, _ in pkgutil.iter_modules([package_dir]):
        if module_name == "__init__":
            continue
            
        module = importlib.import_module(f".{module_name}", package="src.providers")
        
                                                                      
        if hasattr(module, "PROVIDER_ID"):
            PROVIDERS[module.PROVIDER_ID] = module

                                  
load_providers()

def get_provider(provider_id):
    """Pobiera moduł konfiguracyjny dla danego dostawcy."""
    return PROVIDERS.get(provider_id)

def get_all_providers():
    """Zwraca listę wszystkich załadowanych dostawców."""
    return list(PROVIDERS.values())

def detect_provider_by_url(url):
    """Przeszukuje wszystkich dostawców i zwraca ID tego, którego adres pasuje do podanego URL."""
    for provider_id, module in PROVIDERS.items():
        if hasattr(module, "match_url") and module.match_url(url):
            return provider_id
    return "openai"                    

def get_provider_tabs():
    """Zwraca listę nazw wyświetlanych (DISPLAY_NAME) dla zakładek w menu."""
    tabs = []
                                                                                                             
                                                                          
    for module in PROVIDERS.values():
        if hasattr(module, "DISPLAY_NAME"):
            tabs.append(module.DISPLAY_NAME)
    return tabs
