package example;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/orders")
public class OrderController {

    @GetMapping("/{id}")
    public OrderView findOrder(Integer id) {
        return null;
    }

    @PostMapping("")
    public OrderView createOrder(OrderRequest request) {
        return null;
    }

    public void notAnEndpoint() {
    }
}
