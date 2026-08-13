package example;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;

// The target is built from a constant, so it cannot be resolved exactly.
@FeignClient(name = TARGET)
public interface DynamicClient {

    @GetMapping("/things")
    ThingView findThing();
}
