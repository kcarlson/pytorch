from dataclasses import dataclass, field
from torch.fx.graph import Graph
from torch.fx.node import Node
from torch.fx._compatibility import compatibility
from typing import Dict, List, Any, Type
import logging
import os


__all__ = ['get_source_partitions', 'check_subgraphs_connected']

# Set`PYTORCH_MATCHER_LOGLEVEL=INFO` to see debug logs
def _init_logger():
    logger = logging.getLogger(__name__)

    level = os.environ.get('PYTORCH_MATCHER_LOGLEVEL', 'WARNING').upper()
    logger.setLevel(level)
    console = logging.StreamHandler()
    formatter = logging.Formatter("%(filename)s > %(message)s")
    console.setFormatter(formatter)
    console.setLevel(level)
    # add the handlers to the logger
    logger.addHandler(console)
    logger.propagate = False
    return logger

logger = _init_logger()


@compatibility(is_backward_compatible=False)
@dataclass
class SourcePartition():
    # Nodes in a particular partition
    nodes: List[Node]

    # The source these nodes decomposed from
    source: Any

    # Nodes in the graph that are needed as inputs to the partition
    input_nodes: List[Node] = field(default_factory=list)

    # Nodes in the partition that are being used by nodes outside of the
    # partition
    output_nodes: List[Node] = field(default_factory=list)

    # Parameters that are being used
    params: List[str] = field(default_factory=list)


@compatibility(is_backward_compatible=False)
def get_source_partitions(
    graph: Graph,
    wanted_sources: List[Any]
) -> Dict[Any, List[SourcePartition]]:
    """
    Args:
        graph: The graph we want to partition
        wanted_sources: List of sources of nodes that were decomposed from this
            source. This can be a function (ex. torch.nn.functional.linear) or a
            leaf module type (ex. torch.nn.Linear).

    Returns:
        Dictionary mapping sources that were given to a list of SourcePartitions
        that correspond to the list of nodes that were decomposed from the given
        source.
    """
    modules: Dict[Type, Dict[str, List[Node]]] = {}

    for node in graph.nodes:
        # The metadata source_fn should contain a tuple of a unique name for the
        # source, and the source function if the node is decomposed from a
        # function, or the type of module if the node is decomposed from a leaf
        # module

        if (source_fn := node.meta.get("source_fn", None)) is None:
            continue

        if source_fn[1] not in wanted_sources:
            continue

        diff_modules = modules.setdefault(source_fn[1], {})
        partition = diff_modules.setdefault(source_fn[0], [])
        partition.append(node)

    def make_partition(nodes: List[Node], module_type: Type) -> SourcePartition:
        input_nodes = set()
        output_nodes = set()
        params = set()
        for node in nodes:
            for arg in node.args:
                if isinstance(arg, Node) and arg not in nodes:
                    input_nodes.add(arg)

            if node.op == "get_attr":
                params.add(node.target)

            for user in node.users.keys():
                if user not in nodes:
                    output_nodes.add(node)

        return SourcePartition(
            nodes,
            module_type,
            list(input_nodes),
            list(output_nodes),
            list(params),  # type: ignore[arg-type]
        )

    ret: Dict[Type[Any], List[SourcePartition]] = {}
    for k, v in modules.items():
        ret[k] = [make_partition(partition, k) for partition in v.values()]

    return ret


@compatibility(is_backward_compatible=False)
def check_subgraphs_connected(subgraph1: SourcePartition, subgraph2: SourcePartition) -> bool:
    """
    Given two subgraphs A and B (in the form of a list of nodes), checks if
    A has nodes connecting to at least one node in B -- aka there exists a node
    in B that uses a node in A (not the other way around).
    """

    for node in reversed(subgraph1.nodes):
        for user in node.users.keys():
            if user in subgraph2.nodes:
                return True
    return False
