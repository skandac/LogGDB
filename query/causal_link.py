"""
Phase 5, piece 3a: the unified CausalLinkProvider interface.

The whole project claims "the causal graph is the access path." Until now the
operator walked ONE kind of edge (parent_span_id). Cross-request causality adds
another kind (pool-overlap). Rather than special-case pool edges inside
TRACE_CAUSE, we unify: every edge type implements ONE interface, and the operator
walks the graph without knowing what kind of edge it's following.

    predecessors(span_id) -> list[str]   # the causal predecessors of this span

Implementations:
  ParentLinkProvider   : within-trace lineage. predecessors = [parent] or [].
  ResourceLinkProvider : cross-request pool-overlap. predecessors = holders, or [].
  CompositeProvider    : both, merged. THIS is what TRACE_CAUSE walks.

Designed against TWO real implementations (the hybrid plan): the provider join was
built and tested standalone first, so this interface fits actual edge types, not
an imagined one.
"""

from abc import ABC, abstractmethod
from typing import Optional


class CausalLinkProvider(ABC):
    @abstractmethod
    def predecessors(self, span_id: str) -> list[str]:
        """Causal predecessors of span_id. Empty list = no further cause this way."""


class ParentLinkProvider(CausalLinkProvider):
    """Within-trace lineage. The Phase 1 edge, now behind the interface."""

    def __init__(self, parent_of: dict):
        self.parent_of = parent_of

    def predecessors(self, span_id: str) -> list[str]:
        parent = self.parent_of.get(span_id)
        return [] if parent is None else [parent]


class ResourceLinkProviderAdapter(CausalLinkProvider):
    """
    Adapts the standalone ResourceLinkProvider (pool-overlap join) to the
    interface. Wraps it rather than reimplementing — the tested join is untouched.
    """

    def __init__(self, resource_provider):
        self.rp = resource_provider

    def predecessors(self, span_id: str) -> list[str]:
        return self.rp.holders_for(span_id)


class CompositeProvider(CausalLinkProvider):
    """
    Merges several providers. A span's predecessors = the union across all edge
    types. This is what makes the walk cross from a within-trace chain INTO a
    cross-request hop and back into another within-trace chain, transparently.
    """

    def __init__(self, providers: list[CausalLinkProvider]):
        self.providers = providers

    def predecessors(self, span_id: str) -> list[str]:
        seen = set()
        out = []
        for p in self.providers:
            for pred in p.predecessors(span_id):
                if pred not in seen:
                    seen.add(pred)
                    out.append(pred)
        return out