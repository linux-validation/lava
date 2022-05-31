from __future__ import annotations

from typing import Dict, List, Union


def structlog_processor_formatter(
    processor_name: str,
    foreign_pre_chain_names: Union[List[str], Dict[str, Dict[str, str]]],
    *args,
    **kwrags,
):
    import structlog.processors as processors_module
    from structlog.stdlib import ProcessorFormatter

    processors_class = getattr(processors_module, processor_name)
    processor = processors_class()

    from importlib import import_module

    prechains = []
    for n in foreign_pre_chain_names:
        if isinstance(n, str):
            module_name, attribute_name = n.rsplit(".", 1)

            chains_module = import_module(module_name)
            prechains.append(getattr(chains_module, attribute_name))
        elif isinstance(n, dict):
            absolute_path = list(n)[0]

            module_name, attribute_name = absolute_path.rsplit(".", 1)

            chains_module = import_module(module_name)
            chain = getattr(chains_module, attribute_name)
            prechains.append(chain(**n[absolute_path]))
        else:
            raise TypeError

    return ProcessorFormatter(
        processor=processor, foreign_pre_chain=prechains, *args, **kwrags
    )
