"""Example usage of jeeves integration.

Demonstrates how to use the jeeves integration layer for agent execution
with resource quotas and session management.
"""

import asyncio
from stock_screening.integration import (
    get_agent_adapter,
    get_kernel_client,
    get_session_manager,
    ResourceQuota,
    set_user_quota,
)


async def example_basic_usage():
    """Basic example: Run agent via adapter."""
    print("=== Basic Usage ===")

    # Get adapter (automatically uses jeeves if available and enabled)
    adapter = get_agent_adapter(use_jeeves=True)

    # Run agent
    result = await adapter.run_agent(
        agent_type="screening",
        prompt="Find value stocks with low P/E",
        session_id="example_session_123",
        user_id="example_user_456",
    )

    print(f"Result: {result}")
    print()


async def example_with_quota():
    """Example: Set custom quota for user."""
    print("=== Custom Quota ===")

    # Set custom quota for premium user
    premium_quota = ResourceQuota(
        max_llm_calls=100,
        max_output_tokens=200_000,
        max_agent_hops=20,
        max_iterations=50,
    )
    set_user_quota("premium_user", premium_quota)

    adapter = get_agent_adapter(use_jeeves=True)
    result = await adapter.run_agent(
        agent_type="screening",
        prompt="Find growth stocks",
        session_id="premium_session",
        user_id="premium_user",
    )

    print(f"Result: {result}")
    print()


async def example_session_management():
    """Example: Session management."""
    print("=== Session Management ===")

    session_mgr = get_session_manager()

    # Create session
    session_id = await session_mgr.create_session(user_id="user_123")
    print(f"Created session: {session_id}")

    # Get session
    session = await session_mgr.get_session(session_id)
    print(f"Session data: {session}")

    # Update session
    await session_mgr.update_session(session_id, {"state": {"key": "value"}})
    print("Updated session")

    # Clean up
    await session_mgr.delete_session(session_id)
    print("Deleted session")
    print()


async def example_kernel_client():
    """Example: Direct kernel client usage."""
    print("=== Kernel Client ===")

    kernel = get_kernel_client()
    if kernel:
        print("Kernel client available")
        # Create process
        process_id = await kernel.create_process(
            agent_type="screening",
            session_id="kernel_session",
            initial_state={"prompt": "Find value stocks"},
            quota={"max_llm_calls": 50, "max_output_tokens": 100_000},
        )
        print(f"Created process: {process_id}")

        # Schedule process
        await kernel.schedule_process(process_id)
        print("Scheduled process")
    else:
        print("Kernel client not available (jeeves not installed or disabled)")
    print()


async def example_fallback():
    """Example: Fallback to direct execution."""
    print("=== Fallback Behavior ===")

    # Force direct execution (no jeeves)
    adapter = get_agent_adapter(use_jeeves=False)

    result = await adapter.run_agent(
        agent_type="screening",
        prompt="Find value stocks",
        # session_id and user_id not required for direct execution
    )

    print(f"Result (direct execution): {result}")
    print()


async def main():
    """Run all examples."""
    print("Jeeves Integration Examples\n")

    try:
        await example_basic_usage()
        await example_with_quota()
        await example_session_management()
        await example_kernel_client()
        await example_fallback()
    except Exception as e:
        print(f"Error: {e}")
        print("\nNote: Some examples require jeeves-core and Redis to be running.")


if __name__ == "__main__":
    asyncio.run(main())
