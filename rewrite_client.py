import re

with open("elixir/lib/symphony_elixir/linear/client.ex", "r") as f:
    text = f.read()

# We want to replace the `graphql/3` function with a cleaner one.
target_fn = """  @spec graphql(String.t(), map(), keyword()) :: {:ok, map()} | {:error, term()}
  def graphql(query, variables \\\\ %{}, opts \\\\ [])
      when is_binary(query) and is_map(variables) and is_list(opts) do
    payload = build_graphql_payload(query, variables, Keyword.get(opts, :operation_name))
    request_fun = Keyword.get(opts, :request_fun, &post_graphql_request/2)

    with {:ok, headers} <- graphql_headers(),
         {:ok, %{status: 200, body: body}} <- request_fun.(payload, headers) do
      {:ok, body}
    else
      {:ok, response} ->
        Logger.error(
          "Linear GraphQL request failed status=#{response.status}" <>
            linear_error_context(payload, response)
        )

        {:error, {:linear_api_status, response.status}}

      {:error, reason} ->
        Logger.error("Linear GraphQL request failed: #{inspect(reason)}")
        {:error, {:linear_api_request, reason}}
    end
  end"""

replacement_fn = """  @spec graphql(String.t(), map(), keyword()) :: {:ok, map()} | {:error, term()}
  def graphql(query, variables \\\\ %{}, opts \\\\ [])
      when is_binary(query) and is_map(variables) and is_list(opts) do
    payload = build_graphql_payload(query, variables, Keyword.get(opts, :operation_name))
    request_fun = Keyword.get(opts, :request_fun, &post_graphql_request/2)
    max_retries = Keyword.get(opts, :max_retries, 3)

    with {:ok, headers} <- graphql_headers() do
      do_graphql_with_backoff(payload, headers, request_fun, max_retries, 0)
    end
  end

  defp do_graphql_with_backoff(payload, headers, request_fun, max_retries, attempt) do
    case request_fun.(payload, headers) do
      {:ok, %{status: 200, body: body}} ->
        {:ok, body}

      {:ok, %{status: 429} = response} ->
        if attempt < max_retries do
          retry_after = extract_retry_after(response) || backoff_delay(attempt)
          Logger.warning("Tracker API rate limited (429). Retrying in #{retry_after}ms (attempt #{attempt + 1}/#{max_retries})")
          Process.sleep(retry_after)
          do_graphql_with_backoff(payload, headers, request_fun, max_retries, attempt + 1)
        else
          Logger.error("Tracker GraphQL request failed status=429 after #{max_retries} retries." <> linear_error_context(payload, response))
          {:error, {:linear_api_status, 429}}
        end

      {:ok, response} ->
        Logger.error(
          "Tracker GraphQL request failed status=#{response.status}" <>
            linear_error_context(payload, response)
        )
        {:error, {:linear_api_status, response.status}}

      {:error, reason} ->
        if attempt < max_retries do
          delay = backoff_delay(attempt)
          Logger.warning("Tracker GraphQL request failed: #{inspect(reason)}. Retrying in #{delay}ms (attempt #{attempt + 1}/#{max_retries})")
          Process.sleep(delay)
          do_graphql_with_backoff(payload, headers, request_fun, max_retries, attempt + 1)
        else
          Logger.error("Tracker GraphQL request failed: #{inspect(reason)}")
          {:error, {:linear_api_request, reason}}
        end
    end
  end

  defp extract_retry_after(response) do
    case Map.get(response, :headers) do
      headers when is_list(headers) ->
        case get_header(headers, "retry-after") do
          [val] ->
            case Integer.parse(val) do
              {seconds, _} -> seconds * 1000
              _ -> nil
            end
          _ -> nil
        end
      _ -> nil
    end
  end

  defp get_header(headers, key) do
    Enum.find_value(headers, [], fn
      {k, v} when is_binary(k) -> if String.downcase(k) == key, do: [v]
      _ -> nil
    end)
  end

  defp backoff_delay(attempt) do
    1000 * :math.pow(2, attempt) |> trunc()
  end"""

text = text.replace(target_fn, replacement_fn)

with open("elixir/lib/symphony_elixir/linear/client.ex", "w") as f:
    f.write(text)
