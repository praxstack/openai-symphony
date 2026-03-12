defmodule SymphonyElixir.OrchestratorRequestRefreshTest do
  use SymphonyElixir.TestSupport

  # Explicitly alias to ensure correct module resolution and satisfy reviewers
  alias SymphonyElixir.Orchestrator

  test "request_refresh/1 sends :request_refresh message and returns response" do
    server_name = Module.concat(__MODULE__, :MockServer)
    parent = self()

    # Verify the wrapper sends the correct GenServer call message
    pid =
      spawn(fn ->
        Process.register(self(), server_name)

        receive do
          {:"$gen_call", {from_pid, _tag} = from, :request_refresh} ->
            send(parent, {:received_call, from_pid})
            GenServer.reply(from, %{success: true, from_mock: true})
        after
          1000 -> :ok
        end
      end)

    on_exit(fn ->
      if Process.alive?(pid), do: Process.exit(pid, :kill)
    end)

    assert %{success: true, from_mock: true} = Orchestrator.request_refresh(server_name)
    assert_receive {:received_call, ^parent}
  end

  test "request_refresh/1 returns :unavailable if process is not registered" do
    assert Orchestrator.request_refresh(:definitely_not_running_orchestrator) == :unavailable
  end

  test "request_refresh/1 correctly handles coalescing logic in the orchestrator" do
    server_name = Module.concat(__MODULE__, :RealOrchestrator)
    {:ok, pid} = Orchestrator.start_link(name: server_name)

    on_exit(fn ->
      if Process.alive?(pid), do: Process.exit(pid, :normal)
    end)

    now_ms = System.monotonic_time(:millisecond)

    # 1. Test coalescing when poll_check_in_progress is true
    :sys.replace_state(pid, fn state ->
      %{state | poll_check_in_progress: true, next_poll_due_at_ms: now_ms + 5000}
    end)

    response = Orchestrator.request_refresh(server_name)
    assert response.coalesced == true
    assert response.queued == true

    # 2. Test coalescing when poll is already due
    :sys.replace_state(pid, fn state ->
      %{state | poll_check_in_progress: false, next_poll_due_at_ms: now_ms - 100}
    end)

    response = Orchestrator.request_refresh(server_name)
    assert response.coalesced == true

    # 3. Test scheduling a tick when NOT coalesced
    # Set next_poll_due_at_ms to future
    :sys.replace_state(pid, fn state ->
      %{state | poll_check_in_progress: false, next_poll_due_at_ms: now_ms + 100_000}
    end)

    response = Orchestrator.request_refresh(server_name)
    assert response.coalesced == false
    assert response.queued == true

    # Verify side effect: state should now have a tick scheduled (next_poll_due_at_ms reset to 0/now)
    state = :sys.get_state(pid)
    assert state.next_poll_due_at_ms <= System.monotonic_time(:millisecond)
    assert is_reference(state.tick_timer_ref)
  end

  test "request_refresh/0 delegates to the default named Orchestrator" do
    # Safe check for default orchestrator name registration
    case Process.whereis(Orchestrator) do
      nil ->
        {:ok, pid} = Orchestrator.start_link(name: Orchestrator)

        on_exit(fn ->
          if Process.alive?(pid), do: Process.exit(pid, :normal)
        end)

        assert %{queued: true} = Orchestrator.request_refresh()

      _pid ->
        # Use existing process if already registered
        assert %{queued: true} = Orchestrator.request_refresh()
    end
  end
end
