import torch
from torch._guards import detect_fake_mode
from torch.export.exported_program import InputKind, InputSpec, TensorArgument


def lift_constant_tensor_pass(ep):
    """
    Takes an ExportedProgram and returns the ExportedProgram modified in-place,
    with the constant tensors as buffers.
    """
    if len([node for node in ep.graph.nodes if node.op == "placeholder"]) == 0:
        return ep

    graph_signature = ep.graph_signature
    inputs = graph_signature.input_specs

    fake_mode = detect_fake_mode(
        tuple(node.meta["val"] for node in ep.graph.nodes if node.op == "placeholder")
    )
    assert fake_mode is not None

    first_user_input = None
    for node in ep.graph.nodes:
        if node.op == "placeholder" and node.name in graph_signature.user_inputs:
            first_user_input = node
            break

    for node in ep.graph.nodes:
        if node.op == "get_attr":
            constant_tensor = getattr(ep.graph_module, node.target)
            if not isinstance(constant_tensor, torch.Tensor):
                continue

            constant_tensor_fqn = f"_lifted_tensor_constant{len(ep.tensor_constants)}"

            with ep.graph.inserting_before(first_user_input):
                # Insert the constant node before the first user input
                const_placeholder_node = ep.graph.placeholder(constant_tensor_fqn)
                for k, v in node.meta.items():
                    const_placeholder_node.meta[k] = v
                const_placeholder_node.meta["val"] = fake_mode.from_tensor(
                    constant_tensor, static_shapes=True
                )
                const_placeholder_node.meta["val"].constant = constant_tensor
                node.replace_all_uses_with(const_placeholder_node)
                ep.graph.erase_node(node)

                # Add the constant as a buffer to the graph signature
                inputs.append(
                    InputSpec(
                        kind=InputKind.CONSTANT_TENSOR,
                        arg=TensorArgument(name=const_placeholder_node.name),
                        target=constant_tensor_fqn,
                    )
                )
                ep.tensor_constants[constant_tensor_fqn] = constant_tensor

    ep.graph_module.recompile()
    return ep
